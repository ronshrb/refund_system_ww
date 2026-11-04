"""Reviews page — upload, queue, one-row decision, and a review comment.

Part 2 reviewer. A policy match is the only auto-approval. Every match is
checked before it is written. The checkup can send that match back to a
person. It cannot create an approval. Simulated decisions are not refunds.
The upload, the review, and the decision log stay in this Streamlit session.
Nothing on this page is written to disk. Use pending.csv reads
``data/raw/pending.csv`` into that same session upload and does not write it.
The local summary remains available for rows the checkup did not comment on.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.data.data_processing import normalize_reason
from utils.data.dataloader import load_rows
from utils.policy.live_policy import read_live_policy
from utils.policy.llm_provider import key_is_accepted
from utils.review.decisions import (
    auto_display_rows,
    index_decisions,
    record_reviewer_decision,
)
from utils.review.history_lookup import (
    HISTORY_COLUMNS,
    HISTORY_LABEL,
    UNRECOGNIZED_REASON,
    similar_history,
)
from utils.review.sheet import (
    apply_user_decision,
    build_review_sheet,
    parse_sheet_time,
    pending_request_ids,
    review_minutes_since,
    sheet_csv,
)
from utils.review.queue import (
    OPEN_TABLE_COLUMNS,
    UploadFormatError,
    classify_upload,
    detail_fields,
    style_selected_request,
    LATER_COPY_COLUMNS,
    RELATED_COLUMNS,
    duplicate_detail,
    later_copies,
    open_table_rows,
    related_requests,
    repeat_notice,
    read_upload,
    resolve_open_selection,
    search_open_rows,
    upload_ids,
)
from utils.review.review_agent import (
    build_reviewer_comment,
    checkup_match,
    draft_review_summary,
    fill_blank_auto_notes,
)
_HISTORY_PATH = _REPO_ROOT / "data" / "raw" / "history.csv"
_PENDING_SAMPLE = _REPO_ROOT / "data" / "raw" / "pending.csv"


def _empty_selection() -> dict:
    """A fresh selection dict. Do not share the inner list across writes."""
    return {"selection": {"rows": [], "columns": [], "cells": []}}


def _paste_summary() -> None:
    """Copy the review summary into the outcome note. Does not record a decision."""
    text = st.session_state.get("review_summary_text")
    st.session_state["review_outcome_note"] = text if isinstance(text, str) else ""


def _apply_reviewer_action() -> None:
    """Record one simulated decision for the selected open row.

    Runs from the button callback before the page redraws. Does not send
    note text to a model. A blank outcome note, a refused amount, or an
    over-long note writes nothing.
    """
    selected = st.session_state.get("review_selected_id")
    snapshot = st.session_state.get("review_open_snapshot") or []
    row = next((item for item in snapshot if item.get("request_id") == selected), None)
    if row is None:
        st.session_state["review_action_error"] = (
            "Select one open request. Nothing was decided."
        )
        st.session_state["review_action_notice"] = None
        return
    choice = st.session_state.get("review_decision")
    action = {"approved": "approve_full", "rejected": "reject", "partial": "partial"}.get(choice)
    if action is None:
        st.session_state["review_action_error"] = (
            "Choose approved, rejected, or partial. Nothing was decided."
        )
        st.session_state["review_action_notice"] = None
        return
    note = st.session_state.get("review_outcome_note", "")
    if not isinstance(note, str) or not note.strip():
        st.session_state["review_action_error"] = (
            "Outcome note is blank. Nothing was decided."
        )
        st.session_state["review_action_notice"] = None
        return
    decided_at = datetime.now(timezone.utc)
    outcome = record_reviewer_decision(
        records=_decision_log(),
        request_id=row["request_id"],
        action=action,
        requested_amount=row["intake"].get("amount_usd", ""),
        partial_amount=st.session_state.get("review_partial_amount", "") if action == "partial" else "",
        note=note,
        policy_file=st.session_state.get("review_policy_file"),
    )
    if outcome["ok"]:
        _write_user_decision(
            request_id=row["request_id"],
            action=action,
            outcome_note=note.strip(),
            decided_at=decided_at,
        )
        st.session_state["review_action_error"] = None
        st.session_state["review_action_notice"] = (
            f"{row['request_id']} recorded."
        )
        st.session_state["review_selected_id"] = None
        st.session_state["review_open_index"] = None
        st.session_state["review_open_table"] = _empty_selection()
        st.session_state["review_outcome_note"] = ""
        st.session_state["review_partial_amount"] = ""
        return
    st.session_state["review_action_error"] = outcome["error"]
    st.session_state["review_action_notice"] = None


def _cached_summary(payload, *, request_id):
    """Return one review summary for this request id.

    The cache key is the request id. A second Review click in this session
    may reuse it. The summary cannot approve. An empty result lets the
    queue use the local sentence.
    """
    cache = st.session_state.setdefault("review_summary_cache", {})
    if isinstance(request_id, str) and request_id in cache:
        return cache[request_id]
    text = draft_review_summary(payload, chat_model=None)
    if isinstance(request_id, str):
        cache[request_id] = text if isinstance(text, str) else ""
    return text if isinstance(text, str) else ""


def _cached_checkup(payload, *, request_id):
    """Return one checkup for this request id.

    The cache key is the request id. The call uses the checkup payload.
    A second Review click in this session may reuse the stored checkup.
    The checkup cannot approve.
    """
    cache = st.session_state.setdefault("review_agent_cache", {})
    if isinstance(request_id, str) and request_id in cache:
        return cache[request_id]
    checked = checkup_match(payload, chat_model=None)
    if isinstance(request_id, str):
        cache[request_id] = checked
    return checked


def _read_history_rows() -> list:
    """Load history for the checkup. A missing file is an empty list.

    Does not write ``data/raw``.
    """
    try:
        return load_rows(_HISTORY_PATH)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return []


def _decision_log() -> list:
    """The simulated decision log for this session. A new session starts empty."""
    log = st.session_state.get("review_decision_log")
    if not isinstance(log, list):
        log = []
        st.session_state["review_decision_log"] = log
    return log


def _load_stored_upload() -> dict | None:
    """Return the upload kept in this session."""
    stored = st.session_state.get("review_stored_upload")
    if not isinstance(stored, dict):
        return None
    filename = stored.get("filename")
    token = stored.get("token")
    payload = stored.get("payload")
    if not isinstance(filename, str) or not isinstance(token, str):
        return None
    if not isinstance(payload, (bytes, bytearray)):
        return None
    if hashlib.sha256(payload).hexdigest() != token:
        return None
    return stored


def _save_upload(filename: str, payload: bytes) -> dict:
    """Keep one uploaded CSV in this session. A different file clears its review."""
    token = hashlib.sha256(payload).hexdigest()
    previous = _load_stored_upload()
    stored = {"filename": filename, "token": token, "payload": bytes(payload)}
    st.session_state["review_stored_upload"] = stored
    if previous is None or previous["token"] != token:
        st.session_state.pop("review_saved_result", None)
        st.session_state.pop("review_sheet", None)
    return stored


def _load_review_result(token: str) -> dict | None:
    """Return this session's review only when it belongs to this file."""
    payload = st.session_state.get("review_saved_result")
    if not isinstance(payload, dict) or payload.get("token") != token:
        return None
    if not isinstance(payload.get("errors"), list) or not isinstance(payload.get("open"), list):
        return None
    return payload


def _save_review_result(token: str, result: dict, reviewed_at: datetime) -> None:
    """Keep the review in this session so a rerun can show the same queue."""
    st.session_state["review_saved_result"] = {
        "token": token,
        "reviewed_at": reviewed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": result.get("errors") or [],
        "open": result.get("open") or [],
    }


def _save_sheet(token: str, columns: list[str], rows: list[dict]) -> None:
    """Keep the working table in this session. This is not the source CSV."""
    st.session_state["review_sheet"] = {
        "token": token,
        "columns": columns,
        "rows": rows,
    }


def _load_sheet(token: str) -> tuple[list[str], list[dict]]:
    """Return the working table only when it belongs to this file."""
    sheet = st.session_state.get("review_sheet")
    if not isinstance(sheet, dict) or sheet.get("token") != token:
        return [], []
    columns = sheet.get("columns")
    rows = sheet.get("rows")
    if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
        return [], []
    if not isinstance(rows, list) or not rows:
        return [], []
    return columns, rows


def _write_user_decision(
    *,
    request_id: str,
    action: str,
    outcome_note: str,
    decided_at: datetime,
) -> None:
    """Update the session table after a recorded decision."""
    stored = _load_stored_upload()
    if stored is None:
        return
    token = stored["token"]
    columns, rows = _load_sheet(token)
    if not rows:
        return
    saved = st.session_state.get("review_saved_result")
    started = parse_sheet_time(saved.get("reviewed_at")) if isinstance(saved, dict) else None
    updated = apply_user_decision(
        rows,
        request_id=request_id,
        action=action,
        outcome_note=outcome_note,
        decided_at=decided_at,
        review_minutes=review_minutes_since(started, decided_at),
    )
    _save_sheet(token, columns, updated)


def _reset_queue_selection(*, keep_checkup_cache: bool = False) -> None:
    """A new file does not keep the previous row selection.

    A second Review click may keep the checkup cache. A new upload clears it.
    The cache key is the request id.
    """
    st.session_state["review_selected_id"] = None
    st.session_state["review_open_index"] = None
    st.session_state["review_filter_sig"] = None
    st.session_state["review_open_table"] = _empty_selection()
    if not keep_checkup_cache:
        st.session_state["review_agent_cache"] = {}
        st.session_state["review_summary_cache"] = {}


def _picked_index(state):
    if state is None:
        return None
    if isinstance(state, dict):
        selection = state.get("selection")
    else:
        selection = getattr(state, "selection", None)
    if selection is None:
        return None
    if isinstance(selection, dict):
        rows = selection.get("rows")
    else:
        rows = getattr(selection, "rows", None)
    if not rows:
        return None
    index = rows[0]
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    return index


st.title("Reviews")
_live = read_live_policy()
if _live is None:
    st.write("No live policy. Every request goes to review.")

_policy = _live["policy"] if _live else None
st.session_state["review_policy_file"] = _live["file"] if _live else None

_stored = _load_stored_upload()
_sample_error = None
with st.container(horizontal=True, vertical_alignment="center"):
    _upload = st.file_uploader(
        "CSV",
        type=["csv"],
        key="review_upload",
        label_visibility="collapsed",
        width=360,
    )
    if _upload is not None:
        _payload = _upload.getvalue()
        _token = hashlib.sha256(_payload).hexdigest()
        # The widget keeps its last file across reruns. Remember that token so
        # Use pending.csv is not replaced by it. Different bytes still replace
        # the sample. The same bytes are not a new choice.
        _applied = st.session_state.get("review_applied_upload_token")
        if _applied != _token and (_stored is None or _stored["token"] != _token):
            _stored = _save_upload(_upload.name, _payload)
            _reset_queue_selection()
        st.session_state["review_applied_upload_token"] = _token
    if st.button("Use pending.csv"):
        try:
            _sample_payload = _PENDING_SAMPLE.read_bytes()
        except OSError:
            _sample_error = (
                "Could not read data/raw/pending.csv. "
                "The file already in this session was left unchanged."
            )
        else:
            _stored = _save_upload("pending.csv", _sample_payload)
            _reset_queue_selection()
    _key_ready = key_is_accepted(st.session_state)
    _review_clicked = st.button("Review", disabled=_stored is None or not _key_ready)
    if _key_ready:
        st.markdown("Upload a pending CSV or click Use pending.csv, then click Review.")
    else:
        st.markdown(
            "Apply the API key in the sidebar, then upload a file or click Use pending.csv, and click Review."
        )

if _sample_error:
    st.error(_sample_error)

if _stored is not None:
    st.caption(f"File: {_stored['filename']}")

if _review_clicked:
    _reset_queue_selection(keep_checkup_cache=True)
    # A new Review click writes the summary again. The previous comment is not reused.
    st.session_state["review_summary_cache"] = {}
    with st.spinner("The agent is reviewing the requests"):
        try:
            _fieldnames, _rows = read_upload(_stored["payload"])
        except UploadFormatError as error:
            st.error(str(error))
            st.stop()
        _fresh = classify_upload(
            _rows,
            log_records=_decision_log(),
            fieldnames=_fieldnames,
            agent=_cached_checkup,
            summarizer=_cached_summary,
            history_rows=_read_history_rows(),
        )
        _reviewed_at = datetime.now(timezone.utc)
        _previous_rows = _load_sheet(_stored["token"])[1]
        _logged_now = index_decisions(_decision_log())
        _sheet_columns, _sheet_rows = build_review_sheet(
            _rows,
            fieldnames=_fieldnames,
            auto=_fresh.get("auto") or [],
            logged=_logged_now,
            previous=_previous_rows,
            reviewed_at=_reviewed_at,
        )
        fill_blank_auto_notes(_sheet_rows, list(_logged_now.values()))
        _save_sheet(_stored["token"], _sheet_columns, _sheet_rows)
        _save_review_result(_stored["token"], _fresh, _reviewed_at)

if _stored is None:
    st.stop()

try:
    _fieldnames, _rows = read_upload(_stored["payload"])
except UploadFormatError as error:
    st.error(str(error))
    st.stop()

_saved = _load_review_result(_stored["token"])
if _saved is None:
    st.info("Click Review to send this file to the agent.")
    st.stop()

_header_errors = [item for item in _saved["errors"] if item.get("kind") == "missing_header"]
if _header_errors:
    for _item in _header_errors:
        st.error(_item["message"])
    st.stop()

for _item in _saved["errors"]:
    st.error(_item["message"])

_sheet_columns, _sheet_rows = _load_sheet(_stored["token"])
_logged_records = list(index_decisions(_decision_log()).values())
if _sheet_rows and fill_blank_auto_notes(_sheet_rows, _logged_records):
    _save_sheet(_stored["token"], _sheet_columns, _sheet_rows)
_pending_ids = pending_request_ids(_sheet_rows) if _sheet_rows else None
_logged = index_decisions(_decision_log())
# A decision recorded after the review stays decided without running the agent again.
_open_rows = [
    row for row in _saved["open"]
    if row.get("request_id") not in _logged
    and (_pending_ids is None or row.get("request_id") in _pending_ids)
]
_result = {"open": _open_rows, "errors": _saved["errors"]}
st.session_state["review_open_snapshot"] = _open_rows

_records = list(_decision_log())
_ids = upload_ids(_rows)
if _sheet_rows:
    _seen_auto: set[str] = set()
    _auto_rows = []
    for _sheet_row in _sheet_rows:
        if _sheet_row.get("decided_by") != "auto":
            continue
        _auto_id = _sheet_row.get("request_id", "").strip()
        if not _auto_id or _auto_id in _seen_auto:
            continue
        _seen_auto.add(_auto_id)
        _auto_rows.append({
            "request_id": _auto_id,
            "amount": _sheet_row.get("amount_usd") or "",
            "outcome_note": _sheet_row.get("outcome_note") or "",
        })
else:
    _auto_rows = auto_display_rows(_records, _ids)

if st.session_state.get("review_action_error"):
    st.error(st.session_state["review_action_error"])
if st.session_state.get("review_action_notice"):
    st.success(st.session_state["review_action_notice"])

with st.expander("Auto-approved"):
    if _auto_rows:
        st.dataframe(
            pd.DataFrame(
                _auto_rows,
                columns=["request_id", "amount", "outcome_note"],
            ),
            hide_index=True,
            column_config={
                "outcome_note": st.column_config.TextColumn("outcome_note", width="large"),
            },
            alt="Simulated auto-approved requests",
        )
    else:
        st.caption("The auto list is empty.")

_queue_col, _selected_col = st.columns(2, vertical_alignment="top")
with _queue_col:
    st.subheader("Open queue")
    _search = st.text_input("Search", key="review_search")
    _filtered = search_open_rows(_result["open"], _search)
    _visible_ids = [row["request_id"] for row in _filtered]
    _filter_sig = _search if isinstance(_search, str) else ""
    _filters_changed = st.session_state.get("review_filter_sig") != _filter_sig
    _selected_id, _selected_index = resolve_open_selection(
        stored_id=st.session_state.get("review_selected_id"),
        visible_ids=_visible_ids,
        picked_index=_picked_index(st.session_state.get("review_open_table")),
        last_index=st.session_state.get("review_open_index"),
        filters_changed=_filters_changed,
    )
    st.session_state["review_selected_id"] = _selected_id
    st.session_state["review_open_index"] = _selected_index
    st.session_state["review_filter_sig"] = _filter_sig
    if _selected_index is None:
        st.session_state["review_open_table"] = _empty_selection()
    else:
        st.session_state["review_open_table"] = {
            "selection": {"rows": [_selected_index], "columns": [], "cells": []}
        }
    st.dataframe(
        pd.DataFrame(open_table_rows(_filtered), columns=list(OPEN_TABLE_COLUMNS)),
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="review_open_table",
        alt="Open requests",
    )

_selected = next(
    (row for row in _filtered if row["request_id"] == _selected_id),
    None,
)
_later = later_copies(_rows, _selected["request_id"]) if _selected is not None else []
_related = related_requests(_rows, _selected["request_id"], _logged) if _selected is not None else []
with _selected_col:
    st.subheader("Selected request")
    if _selected is None:
        st.caption("Select one open request.")
    else:
        st.table(
            style_selected_request(detail_fields(_selected)),
            border="horizontal",
            hide_header=True,
            alt="Intake fields for the selected request",
        )
        if _later:
            st.caption("Later copy of this request. It was not reviewed separately.")
            st.dataframe(
                pd.DataFrame(_later, columns=list(LATER_COPY_COLUMNS)),
                hide_index=True,
                alt="Later copy of the selected request",
            )
        elif duplicate_detail(_selected["extra_row_count"]):
            st.write(duplicate_detail(_selected["extra_row_count"]))
        if _related:
            st.caption("Same customer, amount, and category.")
            st.dataframe(
                pd.DataFrame(_related, columns=list(RELATED_COLUMNS)),
                hide_index=True,
                alt="Other requests with the same customer, amount, and category",
            )

if _selected is not None:
    _intake = _selected["intake"]
    _saved_comment = _selected.get("reviewer_comment")
    if isinstance(_saved_comment, str) and _saved_comment.strip():
        _comment = _saved_comment.strip()
    else:
        _comment = build_reviewer_comment(
            intake=_intake,
            policy_result={"reason": _selected.get("reason")},
            policy=_policy,
        )
    _notice = repeat_notice(_later, _related)
    if _notice:
        _comment = f"{_comment.rstrip()} {_notice}"
    st.session_state["review_summary_text"] = _comment
    _summary_col, _decision_col = st.columns(2, vertical_alignment="top")
    with _summary_col:
        st.subheader("Review summary")
        st.text(_comment)
        with st.expander("Similar cases"):
            st.caption(HISTORY_LABEL)
            _cleaned = normalize_reason(_intake.get("stated_reason"))
            if _cleaned is None:
                st.write(UNRECOGNIZED_REASON)
            try:
                _history_rows = load_rows(_HISTORY_PATH)
            except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
                _history_rows = []
                st.write("History could not be read.")
            _similar = similar_history(
                _history_rows,
                request_id=_selected["request_id"],
                stated_reason=_intake.get("stated_reason"),
            )
            st.dataframe(
                pd.DataFrame(_similar, columns=list(HISTORY_COLUMNS)),
                hide_index=True,
                alt="Similar past decisions",
            )
    with _decision_col:
        st.subheader("Your decision")
        _choice = st.selectbox(
            "Decision",
            ["approved", "rejected", "partial"],
            key="review_decision",
        )
        if _choice == "partial":
            st.text_input("Amount", key="review_partial_amount")
        st.text_area("Outcome note", key="review_outcome_note")
        st.button("Use review summary", on_click=_paste_summary)
        st.button("Record decision", on_click=_apply_reviewer_action)

if _sheet_rows:
    _download_name = "decisions.csv"
    if _stored is not None and isinstance(_stored.get("filename"), str) and _stored["filename"].lower().endswith(".csv"):
        _download_name = _stored["filename"][:-4] + "-decisions.csv"
    st.download_button(
        "Download decisions",
        data=sheet_csv(_sheet_columns, _sheet_rows),
        file_name=_download_name,
        mime="text/csv",
    )

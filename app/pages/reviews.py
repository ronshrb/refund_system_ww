"""Reviews page — upload, queue, one-row decision, and a review comment.

Part 2 reviewer. A policy match is the only auto-approval. Every match is
checked before it is written. The checkup can send that match back to a
person. It cannot create an approval. Simulated decisions are not refunds.
The local summary remains available for rows the checkup did not comment on.
"""

from __future__ import annotations

import hashlib
import json
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
from utils.review.decisions import (
    DecisionLogError,
    auto_display_rows,
    decided_ids,
    load_decisions,
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
    read_sheet_csv,
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
    fill_blank_auto_notes,
)
from utils.review.upload_session import forget_stored_upload_once

_WORKING = _REPO_ROOT / "data" / "working"
_LOG_PATH = _WORKING / "review_decisions.json"
_HISTORY_PATH = _REPO_ROOT / "data" / "raw" / "history.csv"
_UPLOAD_PATH = _WORKING / "review_upload.csv"
_UPLOAD_META_PATH = _WORKING / "review_upload.json"
_RESULT_PATH = _WORKING / "review_result.json"
_SHEET_PATH = _WORKING / "review_sheet.csv"
_SHEET_META_PATH = _WORKING / "review_sheet.json"

# Once per process. A refresh in this run still reads the stored file.
forget_stored_upload_once([
    _UPLOAD_PATH,
    _UPLOAD_META_PATH,
    _RESULT_PATH,
    _SHEET_PATH,
    _SHEET_META_PATH,
])


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
        _LOG_PATH,
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


def _read_json(path: Path):
    """Return a JSON object from disk, or None when the file is missing or bad."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_stored_upload() -> dict | None:
    """Return the saved pending file. A browser refresh reads this, not the widget."""
    meta = _read_json(_UPLOAD_META_PATH)
    if not isinstance(meta, dict):
        return None
    filename = meta.get("filename")
    token = meta.get("token")
    if not isinstance(filename, str) or not isinstance(token, str):
        return None
    try:
        payload = _UPLOAD_PATH.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(payload).hexdigest() != token:
        return None
    return {"filename": filename, "token": token, "payload": payload}


def _clear_review_result() -> None:
    """Drop the last agent result and its working sheet."""
    for path in (_RESULT_PATH, _SHEET_PATH, _SHEET_META_PATH):
        try:
            path.unlink()
        except OSError:
            continue


def _save_upload(filename: str, payload: bytes) -> dict:
    """Store one uploaded CSV. A different file clears the previous review."""
    _WORKING.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(payload).hexdigest()
    previous = _load_stored_upload()
    _UPLOAD_PATH.write_bytes(payload)
    _UPLOAD_META_PATH.write_text(
        json.dumps({"filename": filename, "token": token}),
        encoding="utf-8",
    )
    if previous is None or previous["token"] != token:
        _clear_review_result()
    return {"filename": filename, "token": token, "payload": payload}


def _load_review_result(token: str) -> dict | None:
    """Return the saved review only when it belongs to this file."""
    payload = _read_json(_RESULT_PATH)
    if not isinstance(payload, dict) or payload.get("token") != token:
        return None
    if not isinstance(payload.get("errors"), list) or not isinstance(payload.get("open"), list):
        return None
    return payload


def _save_review_result(token: str, result: dict, reviewed_at: datetime) -> None:
    """Keep the agent output so a refresh or another page can come back to it."""
    _WORKING.mkdir(parents=True, exist_ok=True)
    _RESULT_PATH.write_text(
        json.dumps({
            "token": token,
            "reviewed_at": reviewed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "errors": result.get("errors") or [],
            "open": result.get("open") or [],
        }),
        encoding="utf-8",
    )


def _save_sheet(token: str, columns: list[str], rows: list[dict]) -> None:
    """Store the working table for this upload. This is not the source CSV."""
    _WORKING.mkdir(parents=True, exist_ok=True)
    _SHEET_PATH.write_text(sheet_csv(columns, rows), encoding="utf-8")
    _SHEET_META_PATH.write_text(
        json.dumps({"token": token, "columns": columns}),
        encoding="utf-8",
    )


def _load_sheet(token: str) -> tuple[list[str], list[dict]]:
    """Return the working table only when it belongs to this file."""
    meta = _read_json(_SHEET_META_PATH)
    if not isinstance(meta, dict) or meta.get("token") != token:
        return [], []
    columns = meta.get("columns")
    if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
        return [], []
    try:
        payload = _SHEET_PATH.read_text(encoding="utf-8")
    except OSError:
        return [], []
    _read_columns, rows = read_sheet_csv(payload)
    if not rows:
        return [], []
    return columns, rows


def _write_user_decision(
    *,
    request_id: str,
    action: str,
    outcome_note: str,
    decided_at: datetime,
) -> None:
    """Update the working table after a recorded decision."""
    meta = _read_json(_UPLOAD_META_PATH)
    token = meta.get("token") if isinstance(meta, dict) else None
    if not isinstance(token, str):
        return
    columns, rows = _load_sheet(token)
    if not rows:
        return
    saved = _read_json(_RESULT_PATH)
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
        if _stored is None or _stored["token"] != _token:
            _stored = _save_upload(_upload.name, _payload)
            _reset_queue_selection()
    _review_clicked = st.button("Review", disabled=_stored is None)
    st.markdown("Upload a pending CSV, then click Review.")

if _stored is not None:
    st.caption(f"File: {_stored['filename']}")

if _review_clicked:
    _reset_queue_selection(keep_checkup_cache=True)
    with st.spinner("The agent is reviewing the requests"):
        try:
            _fieldnames, _rows = read_upload(_stored["payload"])
        except UploadFormatError as error:
            st.error(str(error))
            st.stop()
        _fresh = classify_upload(
            _rows,
            log_path=_LOG_PATH,
            fieldnames=_fieldnames,
            agent=_cached_checkup,
            history_rows=_read_history_rows(),
        )
        _reviewed_at = datetime.now(timezone.utc)
        _previous_rows = _load_sheet(_stored["token"])[1]
        try:
            _logged_now = decided_ids(_LOG_PATH)
        except DecisionLogError:
            _logged_now = {}
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
try:
    _logged_records = list(decided_ids(_LOG_PATH).values())
except DecisionLogError:
    _logged_records = []
if _sheet_rows and fill_blank_auto_notes(_sheet_rows, _logged_records):
    _save_sheet(_stored["token"], _sheet_columns, _sheet_rows)
_pending_ids = pending_request_ids(_sheet_rows) if _sheet_rows else None
try:
    _logged = decided_ids(_LOG_PATH)
except DecisionLogError:
    _logged = {}
# A decision recorded after the review stays decided without running the agent again.
_open_rows = [
    row for row in _saved["open"]
    if row.get("request_id") not in _logged
    and (_pending_ids is None or row.get("request_id") in _pending_ids)
]
_result = {"open": _open_rows, "errors": _saved["errors"]}
st.session_state["review_open_snapshot"] = _open_rows

try:
    _records = load_decisions(_LOG_PATH)
except DecisionLogError:
    _records = []
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

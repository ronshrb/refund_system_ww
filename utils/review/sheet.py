"""Working copy of an uploaded pending file, with decision columns filled in.

The source CSV is not rewritten. Auto-approved rows stay in this table and
are left out of the open queue. A person's decision updates that request's
rows. ``review_minutes`` is blank for auto.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import datetime, timezone

import pandas as pd

from utils.review.queue import POST_DECISION_FIELDS

_PENDING = {"", "pending"}
_ACTION_WORD = {
    "approve_full": "approved",
    "reject": "rejected",
    "partial": "partial",
}


def format_sheet_time(moment: datetime) -> str:
    """Dictionary time: ``YYYY-MM-DD HH:MM`` in UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M")


def review_minutes_since(started: datetime | None, decided: datetime) -> str:
    """Whole minutes from when the row was sent to review until the decision.

    A missing start is blank. A clock that goes backwards is zero.
    """
    if started is None:
        return ""
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if decided.tzinfo is None:
        decided = decided.replace(tzinfo=timezone.utc)
    seconds = (decided.astimezone(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
    if seconds < 0:
        return "0"
    return str(int(seconds // 60))


def parse_sheet_time(value: object) -> datetime | None:
    """Read a sheet time or an ISO log time. Anything else is None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


def build_review_sheet(
    rows,
    *,
    fieldnames: list[str],
    auto: list[Mapping],
    logged: Mapping[str, Mapping],
    previous: list[Mapping] | None,
    reviewed_at: datetime,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return every uploaded row, with decision columns for rows already decided.

    A row already decided on the previous sheet keeps those fields. A new
    auto approval is ``decided_by`` ``auto`` and a blank ``review_minutes``.
    Anything still open is ``pending`` with the other decision fields blank.
    """
    columns = list(fieldnames)
    for name in POST_DECISION_FIELDS:
        if name not in columns:
            columns.append(name)
    auto_notes = {
        item.get("request_id"): item.get("outcome_note") or ""
        for item in auto
        if isinstance(item, Mapping) and isinstance(item.get("request_id"), str)
    }
    kept = _decided_by_id(previous)
    sheet: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        built = {name: _cell(row.get(name)) for name in columns}
        identity = built.get("request_id", "").strip()
        if identity and identity in kept:
            built.update(kept[identity])
            # A blank note is not a decision. A later note can fill it.
            if not built.get("outcome_note", "").strip():
                replacement = _cell(auto_notes.get(identity)).strip() if identity in auto_notes else ""
                if not replacement and identity in logged:
                    replacement = _logged_fields(logged[identity]).get("outcome_note", "").strip()
                if replacement:
                    built["outcome_note"] = replacement
        elif identity and identity in auto_notes:
            built.update(_auto_fields(auto_notes[identity], reviewed_at))
        elif identity and identity in logged:
            built.update(_logged_fields(logged[identity]))
        else:
            built.update(_pending_fields())
        sheet.append(built)
    return columns, sheet


def pending_request_ids(rows: list[Mapping]) -> set[str]:
    """Ids that are still pending. Auto-approved and submitted rows are out."""
    pending: set[str] = set()
    for row in rows:
        decision = _cell(row.get("decision")).strip().casefold()
        identity = _cell(row.get("request_id")).strip()
        if identity and decision in _PENDING:
            pending.add(identity)
    return pending


def apply_user_decision(
    rows: list[Mapping],
    *,
    request_id: str,
    action: str,
    outcome_note: str,
    decided_at: datetime,
    review_minutes: str,
) -> list[dict[str, str]]:
    """Fill every row with this id. Other rows are copied unchanged."""
    word = _ACTION_WORD.get(action, "")
    fields = {
        "decision": word,
        "decided_by": "user",
        "outcome_note": outcome_note.strip(),
        "decided_at": format_sheet_time(decided_at),
        "review_minutes": review_minutes,
    }
    updated = []
    for row in rows:
        copied = {key: _cell(value) for key, value in row.items()}
        if copied.get("request_id", "").strip() == request_id.strip():
            copied.update(fields)
        updated.append(copied)
    return updated


def sheet_csv(columns: list[str], rows: list[Mapping]) -> str:
    """CSV text for the download. Does not write the source pending file."""
    frame = pd.DataFrame(
        [{name: _cell(row.get(name)) for name in columns} for row in rows],
        columns=columns,
    )
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue()


def read_sheet_csv(payload: str) -> tuple[list[str], list[dict[str, str]]]:
    """Read a saved working sheet. A bad file is an empty table."""
    try:
        frame = pd.read_csv(
            io.StringIO(payload),
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, ValueError):
        return [], []
    columns = [str(column) for column in frame.columns]
    return columns, frame.to_dict(orient="records")


def _pending_fields() -> dict[str, str]:
    return {
        "decision": "pending",
        "decided_by": "",
        "outcome_note": "",
        "decided_at": "",
        "review_minutes": "",
    }


def _auto_fields(outcome_note: object, reviewed_at: datetime) -> dict[str, str]:
    note = outcome_note.strip() if isinstance(outcome_note, str) else ""
    return {
        "decision": "approved",
        "decided_by": "auto",
        "outcome_note": note,
        "decided_at": format_sheet_time(reviewed_at),
        "review_minutes": "",
    }


def _logged_fields(record: Mapping) -> dict[str, str]:
    action = record.get("action")
    word = _ACTION_WORD.get(action, "") if isinstance(action, str) else ""
    if not word:
        return _pending_fields()
    decided_by = record.get("decided_by")
    who = "auto" if decided_by == "auto" else "user"
    note = record.get("outcome_note")
    if not isinstance(note, str) or not note:
        note = record.get("note") if isinstance(record.get("note"), str) else ""
    moment = parse_sheet_time(record.get("decided_at"))
    minutes = record.get("review_minutes")
    return {
        "decision": word,
        "decided_by": who,
        "outcome_note": note if isinstance(note, str) else "",
        "decided_at": format_sheet_time(moment) if moment else _cell(record.get("decided_at")),
        "review_minutes": "" if who == "auto" else _cell(minutes),
    }


def _decided_by_id(rows: list[Mapping] | None) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    if not rows:
        return found
    for row in rows:
        identity = _cell(row.get("request_id")).strip()
        decision = _cell(row.get("decision")).strip().casefold()
        if not identity or decision in _PENDING:
            continue
        found.setdefault(identity, {name: _cell(row.get(name)) for name in POST_DECISION_FIELDS})
    return found


def _cell(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)

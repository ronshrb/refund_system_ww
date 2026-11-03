"""Similar past decisions for one open refund request.

History is observed, not ground truth. Some recorded decisions were wrong.
The caller passes the rows; this module does not write ``data/raw``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from utils.data.data_processing import normalize_reason

# Same three shapes data_processing accepts. Time is kept when the value has it.
_DATE_FORMATS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"\d{2}/\d{2}/\d{4}"), "%d/%m/%Y"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"), "%Y-%m-%dT%H:%M:%SZ"),
)

HISTORY_COLUMNS = (
    "request_id",
    "requested_at",
    "amount_usd",
    "decision",
    "decided_by",
    "outcome_note",
)

HISTORY_LABEL = (
    "Observed past decisions, not ground truth. Some were wrong, "
    "and some may have followed a different policy."
)
UNRECOGNIZED_REASON = "The reason was not recognized."
DEFAULT_LIMIT = 8


def similar_history(
    rows,
    *,
    request_id: str,
    stated_reason,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Return up to ``limit`` history rows with the same cleaned stated reason.

    Newest ``requested_at`` first. Unparseable dates sort last. Ties break by
    ``request_id`` descending. The selected request id is excluded. When the
    reason does not clean, the result is empty.
    """
    cleaned = normalize_reason(stated_reason)
    if cleaned is None or limit < 1:
        return []
    selected = request_id.strip() if isinstance(request_id, str) else ""
    matched = []
    try:
        materialized = list(rows)
    except TypeError:
        return []
    for row in materialized:
        if not isinstance(row, Mapping):
            continue
        if normalize_reason(row.get("stated_reason")) != cleaned:
            continue
        identity = row.get("request_id")
        identity_text = identity.strip() if isinstance(identity, str) else ""
        if identity_text == selected:
            continue
        matched.append(row)
    matched.sort(key=_sort_key)
    return [_display_row(row) for row in matched[:limit]]


def _parse_requested_at(value: object) -> datetime | None:
    """Parse the three supported request dates, or return None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern, date_format in _DATE_FORMATS:
        if pattern.fullmatch(text):
            try:
                return datetime.strptime(text, date_format)
            except ValueError:
                return None
    return None


def _sort_key(row: Mapping) -> tuple:
    moment = _parse_requested_at(row.get("requested_at"))
    identity = row.get("request_id")
    identity_text = identity.strip() if isinstance(identity, str) else ""
    # Invert code points so ascending sort puts the higher id first.
    inverted_id = "".join(chr(0x10FFFF - ord(character)) for character in identity_text)
    if moment is None:
        return (1, 0, inverted_id)
    return (0, -moment.timestamp(), inverted_id)


def _display_row(row: Mapping) -> dict[str, str]:
    visible: dict[str, str] = {}
    for key in HISTORY_COLUMNS:
        value = row.get(key, "")
        visible[key] = value if isinstance(value, str) else ""
    return visible

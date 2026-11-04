"""Similar past decisions for one open refund request.

History is observed, not ground truth. Some recorded decisions were wrong.
The caller passes the rows; this module does not write ``data/raw``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from utils.data.data_processing import normalize_reason, parse_amount

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


def customer_history(
    rows,
    *,
    customer_id: str,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Return up to ``limit`` history rows with this customer id.

    Newest ``requested_at`` first, with the same tie-break as
    ``similar_history``. A blank customer id matches nothing. Customer and
    support note text is left out.
    """
    selected = customer_id.strip() if isinstance(customer_id, str) else ""
    if not selected:
        return []
    return _select_history(
        rows,
        limit=limit,
        include=lambda row: _cell(row, "customer_id") == selected,
    )


def request_history(
    rows,
    *,
    request_id: str,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Return history rows with this request id so a duplicate is visible.

    ``similar_history`` leaves the selected request id out. This list puts it
    back. Customer and support note text is left out.
    """
    selected = request_id.strip() if isinstance(request_id, str) else ""
    if not selected:
        return []
    return _select_history(
        rows,
        limit=limit,
        include=lambda row: _cell(row, "request_id") == selected,
    )


def duplicate_history(
    rows,
    *,
    request_id: str,
    customer_id: str,
    amount_usd,
    category: str,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Return history matching this request id or customer/amount/category."""
    if limit < 1:
        return []
    selected_id = request_id.strip() if isinstance(request_id, str) else ""
    selected_key = refund_combination_key({
        "customer_id": customer_id,
        "amount_usd": amount_usd,
        "category": category,
    })
    try:
        materialized = list(rows)
    except TypeError:
        return []
    matched = []
    for row in materialized:
        if not isinstance(row, Mapping):
            continue
        same_id = bool(selected_id) and _cell(row, "request_id") == selected_id
        same_combination = (
            selected_key is not None
            and refund_combination_key(row) == selected_key
        )
        if same_id or same_combination:
            matched.append((row, same_id))
    matched.sort(key=lambda item: _sort_key(item[0]))
    duplicates = []
    for row, same_id in matched[:limit]:
        context = _context_row(row)
        context["category"] = _cell(row, "category")
        context["match_type"] = (
            "request_id" if same_id else "customer_amount_category"
        )
        duplicates.append(context)
    return duplicates


def refund_combination_key(row: Mapping) -> tuple[str, str, str] | None:
    """Canonical customer/amount/category key used to identify repeats."""
    customer = _cell(row, "customer_id")
    category = _cell(row, "category")
    amount = parse_amount(row.get("amount_usd"))
    if not customer or not category or amount is None:
        return None
    cents = amount.quantize(Decimal("0.01"))
    if amount != cents:
        return None
    return customer, format(cents, "f"), category.casefold()


def _select_history(rows, *, limit: int, include) -> list[dict]:
    """Newest matching rows, capped, without customer or support note text."""
    if limit < 1:
        return []
    try:
        materialized = list(rows)
    except TypeError:
        return []
    matched = [row for row in materialized if isinstance(row, Mapping) and include(row)]
    matched.sort(key=_sort_key)
    return [_context_row(row) for row in matched[:limit]]


def _cell(row: Mapping, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


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


def _context_row(row: Mapping) -> dict[str, str]:
    """History fields the checkup may see. Notes are not copied."""
    visible = _display_row(row)
    for key in ("customer_id", "stated_reason"):
        visible[key] = _cell(row, key)
    return visible

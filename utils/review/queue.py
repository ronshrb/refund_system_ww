"""Turn an uploaded pending file into auto, open, and already-decided rows.

An eligible row goes to the review agent. The agent can confirm an auto
approval only when its policy tool returns ``approve_full``. In-file
duplicates and ids already in the decision log never reach the agent. This
module does not write ``data/raw`` or ``policies/live_pointer.json``. Paths
are injected by the caller.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pandas as pd

from utils.data.data_processing import normalize_data, normalize_date_column, parse_amount
from utils.policy.live_policy import read_live_policy
from utils.review.decisions import (
    NOTE_LIMIT,
    DecisionLogError,
    already_paid_ids,
    decided_ids,
    record_auto_approval,
)

# Intake columns required on upload, in file order through fraud_flag.
REQUIRED_HEADERS = (
    "request_id",
    "requested_at",
    "customer_id",
    "tenure_months",
    "prior_refunds_12m",
    "amount_usd",
    "category",
    "payment_method",
    "stated_reason",
    "customer_note",
    "support_note",
    "days_since_delivery",
    "fraud_flag",
)

# Present on the sample files. Dropped before evaluate_live. Never eligibility.
POST_DECISION_FIELDS = (
    "decision",
    "decided_by",
    "outcome_note",
    "decided_at",
    "review_minutes",
)

OPEN_TABLE_COLUMNS = ("request_id", "requested_at", "customer_id")


class UploadFormatError(Exception):
    """The upload is not a CSV table. Nothing was decided."""


def read_upload(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Parse CSV bytes into string rows, the same cell rule as ``load_rows``.

    Does not write the bytes. Raises ``UploadFormatError`` when the bytes are
    not a table.
    """
    try:
        frame = pd.read_csv(
            io.BytesIO(payload),
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
        raise UploadFormatError(
            "That file could not be read as a CSV. Nothing was decided."
        ) from error
    frame = _clean_upload_frame(frame)
    fieldnames = [str(column) for column in frame.columns]
    rows = [
        {str(key): _upload_cell(value) for key, value in record.items()}
        for record in frame.to_dict(orient="records")
    ]
    return fieldnames, rows


def _clean_upload_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Clean requested_at and amount_usd the same way as the history normalizer.

    Other columns stay as uploaded text. An unreadable date or amount becomes
    blank so the row cannot be approved from it. This does not write the file.
    """
    has_date = "requested_at" in frame.columns
    has_amount = "amount_usd" in frame.columns
    if has_date and has_amount:
        return normalize_data(frame)
    if has_date:
        return normalize_date_column(frame, "requested_at")
    if has_amount:
        # normalize_data always cleans both columns. A stand-in date is discarded.
        stand_in = frame.copy()
        stand_in["requested_at"] = "2026-01-01"
        cleaned = normalize_data(stand_in)
        frame = frame.copy()
        frame["amount_usd"] = cleaned["amount_usd"]
    return frame


def _upload_cell(value: object) -> str:
    """String cell for the queue. A missing cleaned value is blank."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None or isinstance(value, bool):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, str) else str(value)


def project_intake(row: Mapping) -> dict[str, str]:
    """Return the 13 intake fields. Post-decision columns are not copied."""
    projected: dict[str, str] = {}
    for key in REQUIRED_HEADERS:
        value = row.get(key, "")
        projected[key] = value if isinstance(value, str) else ""
    return projected


def classify_upload(
    rows,
    *,
    log_path: Path,
    policies_dir: Path | None = None,
    fieldnames: list[str] | None = None,
    clock=None,
    chat_model=None,
    agent=None,
) -> dict:
    """Split uploaded string rows into errors, auto, open, and already decided.

    A missing required header returns an error and does not read or write the
    decision log. A blank request id is an error for that row only. A repeated
    id is one open row (the first copy) and is not sent to the review agent.
    An id already in the log is not decided again and is not reopened. Each
    other id is reviewed once. Auto is recorded only when the agent confirms
    and the policy tool returned ``approve_full`` with reason
    ``matched_allow_rule``. ``chat_model`` is injected by tests. ``agent``,
    when passed, replaces that call so the page can cache one upload.
    """
    try:
        materialized = list(rows)
    except TypeError:
        return _empty_result([_error(
            "unreadable_upload",
            "The upload could not be read. Nothing was decided.",
        )])

    names = _header_names(fieldnames, materialized)
    missing = [header for header in REQUIRED_HEADERS if header not in names]
    if missing:
        # Missing a required header decides nothing and does not touch the log.
        joined = ", ".join(missing)
        return _empty_result([_error(
            "missing_header",
            f"Upload is missing required columns: {joined}. Nothing was decided.",
            columns=missing,
        )])

    try:
        logged = decided_ids(log_path)
        paid = already_paid_ids(log_path)
    except DecisionLogError as error:
        return _empty_result([_error("log_unreadable", str(error))])

    errors: list[dict] = []
    first_row: dict[str, Mapping] = {}
    counts: dict[str, int] = {}
    order: list[str] = []
    for row_number, row in enumerate(materialized, start=1):
        if not isinstance(row, Mapping):
            errors.append(_error(
                "bad_row",
                f"Row {row_number} is not a record. Nothing was decided for it.",
                row_number=row_number,
            ))
            continue
        raw_id = row.get("request_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(_error(
                "blank_request_id",
                f"Row {row_number} has a blank request id. Nothing was decided for it.",
                row_number=row_number,
            ))
            continue
        identity = raw_id.strip()
        counts[identity] = counts.get(identity, 0) + 1
        if identity not in first_row:
            first_row[identity] = row
            order.append(identity)

    live = read_live_policy(policies_dir)
    policy_file = live["file"] if live else None
    auto: list[dict] = []
    open_rows: list[dict] = []
    already: list[dict] = []
    repeats = combination_repeat_ids(materialized)

    for identity in order:
        if identity in logged:
            # Already decided: do not call the agent, do not reopen, do not append.
            already.append(dict(logged[identity]))
            continue
        intake = project_intake(first_row[identity])
        extra = counts[identity] - 1
        if extra > 0:
            # Repeated in this file. First row only. Do not call the agent.
            open_rows.append(_open_row(identity, intake, extra_row_count=extra, reason=None))
            continue
        reviewed = _review_eligible(
            intake,
            policies_dir=policies_dir,
            already_paid_ids=set(paid),
            chat_model=chat_model,
            agent=agent,
        )
        policy_result = reviewed.get("policy_result")
        if not isinstance(policy_result, Mapping):
            policy_result = {}
        tool_approved = (
            policy_result.get("decision") == "approve_full"
            and policy_result.get("reason") == "matched_allow_rule"
        )
        outcome_note = reviewed.get("outcome_note")
        note_ok = (
            isinstance(outcome_note, str)
            and bool(outcome_note.strip())
            and len(outcome_note.strip()) <= NOTE_LIMIT
        )
        # Same customer, amount, and category on another id stays with a person.
        blocked_repeat = identity in repeats
        # The model cannot confirm an approval the policy tool did not return.
        if reviewed.get("confirm_auto") is True and tool_approved and note_ok and not blocked_repeat:
            try:
                stored, created = record_auto_approval(
                    log_path,
                    request_id=identity,
                    amount=policy_result.get("approved_amount"),
                    outcome_note=outcome_note.strip(),
                    policy_file=policy_file,
                    clock=clock,
                )
            except DecisionLogError as error:
                # A failed write is not an approval. Leave the row open.
                errors.append(_error("log_unreadable", str(error)))
                open_rows.append(_open_row(
                    identity,
                    intake,
                    extra_row_count=0,
                    reason="review_agent_held",
                    reviewer_comment=reviewed.get("reviewer_comment"),
                ))
                continue
            if not created:
                already.append(dict(stored))
                continue
            paid.add(identity)
            logged[identity] = stored
            auto.append({
                "request_id": identity,
                "amount": stored.get("amount") or "none",
                "outcome_note": stored.get("outcome_note") or "",
            })
            continue
        if blocked_repeat and tool_approved:
            reason = "possible_repeat"
        else:
            reason = "review_agent_held" if tool_approved else policy_result.get("reason")
        open_rows.append(_open_row(
            identity,
            intake,
            extra_row_count=0,
            reason=reason if isinstance(reason, str) else None,
            reviewer_comment=reviewed.get("reviewer_comment"),
        ))

    return {
        "errors": errors,
        "auto": auto,
        "open": open_rows,
        "already_decided": already,
    }


def upload_ids(rows) -> list[str]:
    """Unique stripped request ids in file order, skipping blanks."""
    seen: list[str] = []
    known: set[str] = set()
    try:
        materialized = list(rows)
    except TypeError:
        return []
    for row in materialized:
        if not isinstance(row, Mapping):
            continue
        raw_id = row.get("request_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        identity = raw_id.strip()
        if identity not in known:
            known.add(identity)
            seen.append(identity)
    return seen


def filter_open_rows(rows: list[dict], filters: Mapping | None = None) -> list[dict]:
    """Return open rows that match every non-blank filter.

    Text filters are case-insensitive substrings. ``stated_reason`` matches
    the raw value or the cleaned reason. ``fraud_flag`` uses the raw value
    only, so a blank flag does not match ``no``. Amount bounds are inclusive
    on ``parse_amount``. A blank bound is open. A row whose amount does not
    parse is left out when either bound is set. If the minimum is greater
    than the maximum, no rows are returned. A non-blank bound that itself
    does not parse also returns no rows.
    """
    from utils.data.data_processing import normalize_reason, parse_amount

    filters = filters or {}
    request_id = _filter_text(filters, "request_id")
    requested_at = _filter_text(filters, "requested_at")
    customer_id = _filter_text(filters, "customer_id")
    category = _filter_text(filters, "category")
    stated_reason = _filter_text(filters, "stated_reason")
    fraud_flag = _filter_text(filters, "fraud_flag")
    minimum, minimum_ok = _amount_bound(filters.get("amount_min"))
    maximum, maximum_ok = _amount_bound(filters.get("amount_max"))
    if not minimum_ok or not maximum_ok:
        return []
    if minimum is not None and maximum is not None and minimum > maximum:
        return []

    kept: list[dict] = []
    for row in rows:
        intake = row.get("intake") if isinstance(row, Mapping) else None
        if not isinstance(intake, Mapping):
            continue
        if not _contains(intake.get("request_id"), request_id):
            continue
        if not _contains(intake.get("requested_at"), requested_at):
            continue
        if not _contains(intake.get("customer_id"), customer_id):
            continue
        if not _contains(intake.get("category"), category):
            continue
        if stated_reason:
            raw_reason = intake.get("stated_reason")
            cleaned = normalize_reason(raw_reason)
            raw_match = _contains(raw_reason, stated_reason)
            cleaned_match = cleaned is not None and stated_reason.casefold() in cleaned.casefold()
            if not raw_match and not cleaned_match:
                continue
        # Blank fraud is not "no": the filter looks at the raw cell only.
        if not _contains(intake.get("fraud_flag"), fraud_flag):
            continue
        if minimum is not None or maximum is not None:
            amount = parse_amount(intake.get("amount_usd"))
            if amount is None:
                continue
            if minimum is not None and amount < minimum:
                continue
            if maximum is not None and amount > maximum:
                continue
        kept.append(row)
    return kept


def search_open_rows(rows: list[dict], query: str | None = None) -> list[dict]:
    """Return open rows whose intake matches one case-insensitive substring.

    A blank query returns every row. A query matches any intake field or the
    cleaned stated reason from ``normalize_reason``.
    """
    from utils.data.data_processing import normalize_reason

    needle = query.strip() if isinstance(query, str) else ""
    if needle == "":
        return list(rows)
    folded = needle.casefold()
    kept: list[dict] = []
    for row in rows:
        intake = row.get("intake") if isinstance(row, Mapping) else None
        if not isinstance(intake, Mapping):
            continue
        matched = False
        for value in intake.values():
            text = value if isinstance(value, str) else ""
            if folded in text.casefold():
                matched = True
                break
        if not matched:
            cleaned = normalize_reason(intake.get("stated_reason"))
            if cleaned is not None and folded in cleaned.casefold():
                matched = True
        if matched:
            kept.append(row)
    return kept


def open_table_rows(rows: list[dict]) -> list[dict]:
    """The three columns the open table shows."""
    visible = []
    for row in rows:
        intake = row.get("intake") or {}
        visible.append({column: intake.get(column, "") for column in OPEN_TABLE_COLUMNS})
    return visible


def detail_fields(open_row: Mapping) -> list[tuple[str, str]]:
    """All 13 intake fields, in upload order, for the selected row."""
    intake = open_row.get("intake") if isinstance(open_row, Mapping) else None
    if not isinstance(intake, Mapping):
        intake = {}
    return [(key, intake.get(key, "") if isinstance(intake.get(key, ""), str) else "") for key in REQUIRED_HEADERS]


def style_selected_request(fields: list[tuple[str, str]]):
    """Key-value table for the selected request. A fraud flag of yes is red."""
    frame = pd.DataFrame(
        {"value": [value for _key, value in fields]},
        index=[key for key, _value in fields],
    )

    def paint(shown: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=shown.index, columns=shown.columns)
        if "fraud_flag" not in shown.index:
            return styles
        flag = shown.at["fraud_flag", "value"]
        if isinstance(flag, str) and flag.strip().casefold() == "yes":
            styles.at["fraud_flag", "value"] = "color: red"
        return styles

    return frame.style.apply(paint, axis=None)


LATER_COPY_COLUMNS = (
    "requested_at",
    "amount_usd",
    "customer_note",
    "support_note",
    "days_since_delivery",
)
RELATED_COLUMNS = (
    "request_id",
    "requested_at",
    "amount_usd",
    "customer_note",
    "support_note",
    "status",
)


def combination_repeat_ids(rows) -> set[str]:
    """Request ids that share a customer, amount, and category with another id.

    The first row of each id supplies the key. A blank customer, category, or
    unreadable amount does not join a group. The same request id is not a
    member of this set.
    """
    groups: dict[tuple[str, str, str], list[str]] = {}
    seen: set[str] = set()
    try:
        materialized = list(rows)
    except TypeError:
        return set()
    for row in materialized:
        if not isinstance(row, Mapping):
            continue
        identity = _request_identity(row.get("request_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        key = _combination_key(row)
        if key is None:
            continue
        groups.setdefault(key, []).append(identity)
    found: set[str] = set()
    for identities in groups.values():
        if len(identities) > 1:
            found.update(identities)
    return found


def later_copies(rows, request_id: str) -> list[dict[str, str]]:
    """Intake rows after the first copy of this request id."""
    copies: list[dict[str, str]] = []
    seen = False
    try:
        materialized = list(rows)
    except TypeError:
        return []
    for row in materialized:
        if not isinstance(row, Mapping):
            continue
        if _request_identity(row.get("request_id")) != request_id:
            continue
        if not seen:
            seen = True
            continue
        copies.append(project_intake(row))
    return copies


def related_requests(rows, request_id: str, decided: Mapping) -> list[dict[str, str]]:
    """Other ids with the same customer, amount, and category.

    ``status`` is open, or the logged decision word when that id is decided.
    """
    first: dict[str, Mapping] = {}
    try:
        materialized = list(rows)
    except TypeError:
        return []
    for row in materialized:
        if not isinstance(row, Mapping):
            continue
        identity = _request_identity(row.get("request_id"))
        if identity and identity not in first:
            first[identity] = row
    current = first.get(request_id)
    if current is None:
        return []
    key = _combination_key(current)
    if key is None:
        return []
    related = []
    for identity, row in first.items():
        if identity == request_id or _combination_key(row) != key:
            continue
        intake = project_intake(row)
        related.append({
            "request_id": identity,
            "requested_at": intake["requested_at"],
            "amount_usd": intake["amount_usd"],
            "customer_note": intake["customer_note"],
            "support_note": intake["support_note"],
            "status": _logged_status(decided.get(identity) if isinstance(decided, Mapping) else None),
        })
    return related


def repeat_notice(later: list[Mapping], related: list[Mapping]) -> str:
    """Sentence for the review summary. Empty when nothing else matches."""
    parts: list[str] = []
    if later:
        note = ""
        for copy in later:
            text = copy.get("customer_note")
            if isinstance(text, str) and text.strip():
                note = text.strip().rstrip(".")
                break
        sentence = "A later copy of this request is in the file and was not reviewed separately."
        if note:
            sentence = f"{sentence} Customer note says {note}."
        parts.append(sentence)
    if related:
        described = [
            f"{item.get('request_id')} is {item.get('status')}"
            for item in related
            if item.get("request_id")
        ]
        if described:
            parts.append(
                "Another request has the same customer, amount, and category: "
                + ", ".join(described)
                + ". Deciding this one does not decide the other."
            )
    return " ".join(parts)


def _request_identity(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _combination_key(row: Mapping) -> tuple[str, str, str] | None:
    customer = row.get("customer_id")
    category = row.get("category")
    if not isinstance(customer, str) or not customer.strip():
        return None
    if not isinstance(category, str) or not category.strip():
        return None
    amount = parse_amount(row.get("amount_usd"))
    if amount is None:
        return None
    cents = amount.quantize(Decimal("0.01"))
    if amount != cents:
        return None
    return (customer.strip(), format(cents, "f"), category.strip().casefold())


def _logged_status(record: object) -> str:
    if not isinstance(record, Mapping):
        return "open"
    action = record.get("action")
    if action == "approve_full":
        return "approved"
    if action == "reject":
        return "rejected"
    if action == "partial":
        return "partial"
    return "open"


def duplicate_detail(extra_row_count: int) -> str | None:
    """Sentence for later copies that were not decided. None when there are none."""
    if extra_row_count <= 0:
        return None
    return f"Extra rows: {extra_row_count}. Later rows were not decided."


def resolve_open_selection(
    *,
    stored_id: str | None,
    visible_ids: list[str],
    picked_index: int | None,
    last_index: int | None,
    filters_changed: bool,
) -> tuple[str | None, int | None]:
    """Return the one selected open id, or clear it when the filter hid it.

    A filter change does not adopt a leftover table index. A click does, and
    only one id is returned.
    """
    visible = [request_id for request_id in visible_ids if isinstance(request_id, str)]
    visible_set = set(visible)
    if stored_id and stored_id not in visible_set:
        return None, None
    user_picked = (
        not filters_changed
        and isinstance(picked_index, int)
        and not isinstance(picked_index, bool)
        and picked_index != last_index
        and 0 <= picked_index < len(visible)
    )
    if user_picked:
        return visible[picked_index], picked_index
    if stored_id in visible_set:
        return stored_id, visible.index(stored_id)
    return None, None


def _open_row(
    identity: str,
    intake: dict[str, str],
    *,
    extra_row_count: int,
    reason: str | None,
    reviewer_comment: object = None,
) -> dict:
    row = {
        "request_id": identity,
        "intake": intake,
        "extra_row_count": extra_row_count,
        "duplicate": extra_row_count > 0,
        "reason": reason,
    }
    if isinstance(reviewer_comment, str) and reviewer_comment.strip():
        row["reviewer_comment"] = reviewer_comment
    return row


def _review_eligible(
    intake: Mapping,
    *,
    policies_dir: Path | None,
    already_paid_ids,
    chat_model,
    agent,
) -> dict:
    """Run the review agent, or the page's cached callable, for one row.

    A failure is not an approval. The returned mapping never has
    ``confirm_auto`` true unless the agent itself said so.
    """
    try:
        if agent is not None:
            reviewed = agent(
                intake,
                already_paid_ids=already_paid_ids,
                policies_dir=policies_dir,
            )
        else:
            from utils.review.review_agent import review_eligible_row

            reviewed = review_eligible_row(
                intake,
                policies_dir=policies_dir,
                already_paid_ids=already_paid_ids,
                chat_model=chat_model,
            )
    except Exception:
        return {
            "confirm_auto": False,
            "outcome_note": "",
            "reviewer_comment": (
                "Review agent — not a decision.\n"
                "This request was not auto-approved."
            ),
            "policy_result": {},
        }
    if not isinstance(reviewed, Mapping):
        return {
            "confirm_auto": False,
            "outcome_note": "",
            "reviewer_comment": "",
            "policy_result": {},
        }
    return dict(reviewed)


def _header_names(fieldnames, rows: list) -> list[str]:
    if fieldnames is not None:
        return [name for name in fieldnames if isinstance(name, str)]
    for row in rows:
        if isinstance(row, Mapping):
            return [name for name in row.keys() if isinstance(name, str)]
    return []


def _empty_result(errors: list[dict]) -> dict:
    return {"errors": errors, "auto": [], "open": [], "already_decided": []}


def _error(kind: str, message: str, **extra) -> dict:
    payload = {"kind": kind, "message": message}
    payload.update(extra)
    return payload


def _filter_text(filters: Mapping, key: str) -> str:
    value = filters.get(key, "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _contains(haystack: object, needle: str) -> bool:
    if needle == "":
        return True
    text = haystack if isinstance(haystack, str) else ""
    return needle.casefold() in text.casefold()


def _amount_bound(value: object) -> tuple[object, bool]:
    from decimal import Decimal

    from utils.data.data_processing import parse_amount

    if value is None:
        return None, True
    if not isinstance(value, str):
        return None, False
    if not value.strip():
        return None, True
    parsed = parse_amount(value.strip())
    if parsed is None or not isinstance(parsed, Decimal):
        return None, False
    return parsed, True

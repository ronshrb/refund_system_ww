"""Turn an uploaded pending file into auto, open, and already-decided rows.

``evaluate_live`` is the only approval. Every remaining policy match is
sent to the review checkup before it is written. The checkup may send that
match back to a person. It cannot create an approval. A customer note, a
support note, a fraud flag that is not a clear no, a non-match, an in-file
duplicate, an id already in the log, and a same-customer amount-and-category
repeat are not sent to the model and are not auto-approved. This module
does not write ``data/raw`` or ``policies/live_pointer.json``. Paths are
injected by the caller.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pandas as pd

from utils.data.data_processing import normalize_data, normalize_date_column, parse_amount
from utils.policy.live_policy import evaluate_live, explain_rule, read_live_policy
from utils.policy.policy_evaluator import fraud_flag_is_raised
from utils.review.decisions import (
    DecisionLogError,
    already_paid_ids,
    decided_ids,
    index_decisions,
    paid_ids_in,
    record_auto_approval,
)
from utils.review.history_lookup import duplicate_history, refund_combination_key
from utils.review.review_agent import (
    CHECKUP_INCOMPLETE_COMMENT,
    build_checkup_payload,
    build_reviewer_comment,
    build_summary_payload,
    checkup_match,
    comment_directs_approval_or_skip,
    draft_review_summary,
    quoted_note,
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
    log_path: Path | None = None,
    log_records: list | None = None,
    policies_dir: Path | None = None,
    fieldnames: list[str] | None = None,
    clock=None,
    chat_model=None,
    agent=None,
    summarizer=None,
    history_rows=None,
) -> dict:
    """Split uploaded string rows into errors, auto, open, and already decided.

    A missing required header returns an error and does not read or write the
    decision log. A blank request id is an error for that row only. A repeated
    id is one open row (the first copy) and is not evaluated. An id already
    in the log is not decided again and is not reopened. Each other id is
    evaluated once in Python before any model call. Auto is recorded only for
    ``approve_full`` with reason ``matched_allow_rule``, and only after the
    checkup returns ``hold`` exactly false. Every such match is checked,
    including one with no review note. The model may send the match to a
    person. It cannot create an approval. ``history_rows`` defaults to an
    empty list and is not written. ``chat_model`` is injected by tests.
    ``agent``, when passed, replaces the checkup so the page can cache one
    request id. It is not called for a row the policy did not match.
    ``summarizer``, when passed, writes the review summary for an open row
    and can be cached by request id. A summary cannot approve. An empty or
    obedient summary uses the local sentence. ``log_records`` is an
    in-memory log. When it is passed, this function does not read or write
    a file. ``log_path`` is used only when no list is passed.
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

    if log_records is None and not isinstance(log_path, Path):
        return _empty_result([_error(
            "log_unreadable",
            "The decision log could not be read. Nothing was decided.",
        )])
    try:
        if log_records is not None:
            logged = index_decisions(log_records)
            paid = paid_ids_in(log_records)
        else:
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
    policy = live["policy"] if isinstance(live, Mapping) else None
    auto: list[dict] = []
    open_rows: list[dict] = []
    already: list[dict] = []
    repeats = combination_repeat_ids(materialized)
    try:
        history = [] if history_rows is None else list(history_rows)
    except TypeError:
        history = []
    # Policy labels are computed in Python before any model call. A label is
    # not an approval. Duplicates and already-logged ids are not evaluated.
    evaluated = _evaluate_upload_rows(
        order,
        first_row,
        counts,
        logged,
        paid,
        policies_dir,
    )

    for identity in order:
        if identity in logged:
            # Already decided: do not evaluate again, do not reopen, do not append.
            already.append(dict(logged[identity]))
            continue
        intake = project_intake(first_row[identity])
        extra = counts[identity] - 1
        if extra > 0:
            # Repeated in this file. First row only. Do not evaluate or pay.
            open_rows.append(_open_row(
                identity,
                intake,
                extra_row_count=extra,
                reason=None,
                reviewer_comment=_open_summary(
                    identity,
                    intake,
                    {},
                    policy,
                    history,
                    chat_model=chat_model,
                    summarizer=summarizer,
                ),
            ))
            continue
        result = evaluated[identity]
        matched = _is_policy_match(result)
        history_duplicates = duplicate_history(
            history,
            request_id=identity,
            customer_id=intake.get("customer_id", ""),
            amount_usd=intake.get("amount_usd", ""),
            category=intake.get("category", ""),
        )
        # Same customer, amount, and category stays with a person even when
        # the policy matches. The summary cannot pay.
        if identity in repeats or history_duplicates:
            reason = "possible_repeat" if matched else _reason_text(result)
            open_rows.append(_open_row(
                identity,
                intake,
                extra_row_count=0,
                reason=reason,
                reviewer_comment=_open_summary(
                    identity,
                    intake,
                    result,
                    policy,
                    history,
                    chat_model=chat_model,
                    summarizer=summarizer,
                ),
            ))
            continue
        if not matched:
            # A model reply cannot pay a row the policy did not match.
            open_rows.append(_open_row(
                identity,
                intake,
                extra_row_count=0,
                reason=_reason_text(result),
                reviewer_comment=_open_summary(
                    identity,
                    intake,
                    result,
                    policy,
                    history,
                    chat_model=chat_model,
                    summarizer=summarizer,
                ),
            ))
            continue
        # The agent is the last step for a policy match, including one with
        # no review note. hold false records the policy approval. Anything
        # else leaves the row open and writes nothing.
        payload = build_checkup_payload(
            intake=intake,
            policy_result=result,
            policy=policy if isinstance(policy, Mapping) else None,
            rule_sentence=_explain_matched_rule(policy, result.get("matched_rule")),
            history_rows=history,
        )
        checked = _run_checkup(identity, payload, chat_model=chat_model, agent=agent)
        if checked.get("status") == "clear":
            _store_match(
                auto, open_rows, already, errors, logged, paid,
                log_path=log_path,
                log_records=log_records,
                identity=identity,
                intake=intake,
                result=result,
                policy=policy,
                policy_file=policy_file,
                clock=clock,
            )
            continue
        if checked.get("status") == "hold":
            open_rows.append(_open_row(
                identity,
                intake,
                extra_row_count=0,
                reason="review_note_hold",
                reviewer_comment=checked.get("reviewer_comment"),
            ))
            continue
        open_rows.append(_open_row(
            identity,
            intake,
            extra_row_count=0,
            reason="checkup_incomplete",
            reviewer_comment=checked.get("reviewer_comment") or CHECKUP_INCOMPLETE_COMMENT,
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
    """Key-value table for the selected request. A raised fraud flag is red.

    Raised tokens are the same set the evaluator uses: yes, y, and true.
    """
    frame = pd.DataFrame(
        {"value": [value for _key, value in fields]},
        index=[key for key, _value in fields],
    )

    def paint(shown: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=shown.index, columns=shown.columns)
        if "fraud_flag" not in shown.index:
            return styles
        if fraud_flag_is_raised(shown.at["fraud_flag", "value"]):
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
                note = text
                break
        sentence = "A later copy of this request is in the file and was not reviewed separately."
        if note:
            # Same caution as an intake note: an instruction is not an order.
            sentence = f"{sentence} {quoted_note('Customer note', note)}."
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
    return refund_combination_key(row)


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


def _evaluate_intake(intake: Mapping, paid, policies_dir: Path | None) -> dict:
    """Run ``evaluate_live`` in Python. A failure is not an approval."""
    try:
        result = evaluate_live(
            intake,
            already_paid_ids=set(paid),
            policies_dir=policies_dir,
        )
    except Exception:
        return {
            "decision": "review",
            "reason": "invalid_request",
            "matched_rule": None,
            "approved_amount": None,
        }
    return result if isinstance(result, Mapping) else {
        "decision": "review",
        "reason": "invalid_request",
        "matched_rule": None,
        "approved_amount": None,
    }


def _evaluate_upload_rows(order, first_row, counts, logged, paid, policies_dir):
    """Evaluate each eligible id once; duplicate ids and logged rows stay out."""
    evaluated: dict[str, Mapping] = {}
    for identity in order:
        if identity in logged or counts[identity] > 1:
            continue
        result = _evaluate_intake(project_intake(first_row[identity]), paid, policies_dir)
        evaluated[identity] = result
    return evaluated


def _is_policy_match(result: Mapping) -> bool:
    return (
        result.get("decision") == "approve_full"
        and result.get("reason") == "matched_allow_rule"
    )


def _reason_text(result: Mapping) -> str | None:
    reason = result.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason
    return None


def _local_comment(intake: Mapping, result: Mapping, policy) -> str:
    return build_reviewer_comment(
        intake=intake,
        policy_result=result,
        policy=policy if isinstance(policy, Mapping) else None,
    )


def _open_summary(
    identity: str,
    intake: Mapping,
    result: Mapping,
    policy,
    history,
    *,
    chat_model,
    summarizer,
) -> str:
    """The agent's review summary, or the local sentence when that call fails.

    The summary is not a decision. A reply cannot move the row to auto.
    """
    payload = build_summary_payload(
        intake=intake,
        policy_result=result if isinstance(result, Mapping) else {},
        policy=policy if isinstance(policy, Mapping) else None,
        history_rows=history,
    )
    text = ""
    try:
        if summarizer is not None:
            text = summarizer(payload, request_id=identity)
        elif chat_model is not None:
            text = draft_review_summary(payload, chat_model=chat_model)
    except Exception:
        text = ""
    if isinstance(text, str) and text.strip():
        return text.strip()
    return _local_comment(intake, result if isinstance(result, Mapping) else {}, policy)


def _explain_matched_rule(policy, matched_rule: object) -> str:
    """Plain sentence for the allow rule ``evaluate_live`` matched."""
    if not isinstance(policy, Mapping) or not isinstance(matched_rule, str):
        return ""
    allow = policy.get("allow")
    if not isinstance(allow, list):
        return ""
    for rule in allow:
        if isinstance(rule, Mapping) and rule.get("id") == matched_rule:
            sentence = explain_rule(rule)
            return sentence.strip() if isinstance(sentence, str) else ""
    return ""


def _store_match(
    auto: list[dict],
    open_rows: list[dict],
    already: list[dict],
    errors: list[dict],
    logged: dict,
    paid: set,
    *,
    log_path: Path | None,
    log_records: list | None,
    identity: str,
    intake: Mapping,
    result: Mapping,
    policy,
    policy_file: str | None,
    clock,
) -> None:
    """Record the policy match. A refused write stays open and unpaid.

    The outcome note is ``explain_rule`` for the matched rule. The model
    comment is not stored. Customer and support text are not stored.
    """
    rule_id = result.get("matched_rule")
    sentence = _explain_matched_rule(policy, rule_id)
    amount = result.get("approved_amount")
    if not isinstance(amount, Decimal):
        amount = None
    if not isinstance(rule_id, str) or not sentence:
        open_rows.append(_open_row(
            identity,
            dict(intake),
            extra_row_count=0,
            reason="review_agent_held",
            reviewer_comment=_local_comment(intake, result, policy),
        ))
        return
    try:
        stored, created = record_auto_approval(
            log_path,
            records=log_records,
            request_id=identity,
            amount=amount,
            outcome_note=sentence,
            matched_rule=rule_id,
            policy_file=policy_file,
            policy_version=result.get("policy_version"),
            clock=clock,
        )
    except DecisionLogError as error:
        # A failed write is not an approval. Leave the row open.
        errors.append(_error("log_unreadable", str(error)))
        open_rows.append(_open_row(
            identity,
            dict(intake),
            extra_row_count=0,
            reason="review_agent_held",
            reviewer_comment=_local_comment(intake, result, policy),
        ))
        return
    if not created:
        already.append(dict(stored))
        return
    paid.add(identity)
    logged[identity] = stored
    auto.append({
        "request_id": identity,
        "amount": stored.get("amount") or "none",
        "outcome_note": stored.get("outcome_note") or "",
    })


def _run_checkup(identity: str, payload: Mapping, *, chat_model, agent) -> dict:
    """One checkup call. Any result other than hold false leaves the match unpaid."""
    try:
        if agent is not None:
            checked = agent(payload, request_id=identity)
        else:
            checked = checkup_match(payload, chat_model=chat_model)
    except Exception:
        checked = None
    if not isinstance(checked, Mapping):
        return {"status": "incomplete", "reviewer_comment": CHECKUP_INCOMPLETE_COMMENT}
    if checked.get("status") == "clear":
        # The model comment is not an approval and is not kept.
        return {"status": "clear", "reviewer_comment": ""}
    comment = checked.get("reviewer_comment")
    # A hold comment that tells the reviewer to approve or skip review is not accepted.
    if (
        checked.get("status") == "hold"
        and isinstance(comment, str)
        and comment.strip()
        and not comment_directs_approval_or_skip(comment)
    ):
        return {"status": "hold", "reviewer_comment": comment}
    return {"status": "incomplete", "reviewer_comment": CHECKUP_INCOMPLETE_COMMENT}


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

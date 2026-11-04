"""Live policy pointer for deterministic intake.

The pointer file ``policies/live_pointer.json`` is not a policy. It only names
which policy file intake should evaluate. Missing, unreadable, or invalid
pointers fail closed to human review. This module does not approve refunds on
its own, rewrite source CSVs, or let a model write the pointer.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from numbers import Integral
from pathlib import Path

from utils.data.data_processing import parse_amount
from utils.policy.policy_evaluator import evaluate
from utils.policy.policy_preview import PolicyInputError, parse_policy_file

# Handling cost the manager asked to add to every auto refund. Not a field in the CSVs.
AUTO_REFUND_FEE = Decimal("25")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICIES_DIR = PROJECT_ROOT / "policies"
LIVE_POINTER_NAME = "live_pointer.json"
# Blank starter for the Policy Builder. Not a policy the manager can select.
TEMPLATE_POLICY_NAME = "policy.template.json"

# Same post-decision columns preview_matching_rows refuses to treat as intake.
_POST_DECISION_FIELDS = frozenset({
    "decision",
    "decided_by",
    "outcome_note",
    "decided_at",
    "review_minutes",
})

# Column order of data/raw/history.csv. The download uses this order.
HISTORY_COLUMNS = (
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
    "decision",
    "decided_by",
    "outcome_note",
    "decided_at",
    "review_minutes",
)
# Last five history columns, from decision through review_minutes. On an
# auto-approved row the download writes approved / auto / the recorded
# timestamp and leaves the note and minutes blank. Every other row leaves
# all five blank.
_DOWNLOAD_VALUE_COLUMNS = HISTORY_COLUMNS[-5:]

# Closed-schema operators, in manager-sentence order. Unknown keys are not listed.
_OPERATOR_ORDER = (
    "stated_reason",
    "days_since_delivery_gte",
    "days_since_delivery_lte",
    "amount_usd_gt",
    "amount_usd_lte",
    "tenure_months_gte",
    "fraud_flag",
    "customer_id_present",
    "customer_note_blank",
    "support_note_blank",
    "request_id_not_already_paid",
)

_REASON_PHRASES = {
    "changed_mind": "Changed mind",
    "wrong_size": "Wrong size",
    "arrived_damaged": "Arrived damaged",
    "wrong_item": "Wrong item",
    "never_arrived": "Never arrived",
    "late_delivery": "Late delivery",
}

_NO_LIVE_RESULT = {
    "decision": "review",
    "reason": "no_live_policy",
    "matched_rule": None,
    "policy_version": None,
    "approved_amount": None,
}


def list_policy_files(policies_dir: Path | None = None) -> list[dict[str, str | None]]:
    """Return policy JSON files and their status, excluding non-policies.

    ``live_pointer.json`` only names the live file. ``policy.template.json`` is
    the Policy Builder's blank starting draft. Neither is selectable. Example
    files are included. A file that cannot be read still appears, with status
    ``None``.
    """
    directory = _policies_directory(policies_dir)
    if not directory.is_dir():
        return []
    hidden = {LIVE_POINTER_NAME, TEMPLATE_POLICY_NAME}
    items = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        if path.name in hidden:
            continue
        items.append({"file": path.name, "status": _status_string(path)})
    items.sort(key=lambda item: item["file"])
    return items


def read_live_policy(policies_dir: Path | None = None) -> dict | None:
    """Return ``{"policy", "file"}`` for a valid live policy, or None.

    None means there is no usable live policy: the pointer is missing,
    unreadable, names a missing file, leaves the policies directory, or points
    at JSON the evaluator will not accept. An empty allow list is still
    returned; callers must not treat that as permission to auto-approve.
    """
    directory = _policies_directory(policies_dir)
    pointer_path = directory / LIVE_POINTER_NAME
    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    file_name = payload.get("file")
    if not _is_direct_policy_name(file_name):
        return None
    policy_path = directory / file_name
    try:
        if policy_path.resolve().parent != directory.resolve():
            return None
    except OSError:
        return None
    if not policy_path.is_file():
        return None
    try:
        policy = parse_policy_file(policy_path)
    except PolicyInputError:
        return None
    return {"policy": policy, "file": file_name}


def live_file_select_index(file_names: list[str], live_file: str | None) -> int:
    """Return the Policy Picker index for the live file, or 0.

    ``live_file`` is the name from a usable live policy. None, a blank, or a
    name that is not in ``file_names`` keeps the first option.
    """
    if live_file and live_file in file_names:
        return file_names.index(live_file)
    return 0


def make_live_button_disabled(selected_file: str | None, live_file: str | None) -> bool:
    """Return whether Make live should stay disabled.

    Disabled only when the selected file is already the usable live file.
    No usable live file leaves the button enabled.
    """
    if not live_file:
        return False
    return selected_file == live_file


def make_live(file_name: str, *, policies_dir: Path | None = None) -> dict[str, str]:
    """Point the live pointer at one listed policy file.

    Writes ``live_pointer.json`` only. Does not change that file's status,
    rewrite source CSVs, or approve a refund. Refuses path traversal, the
    pointer's own name, and any name that is not a listed policy file.
    A second call replaces the same pointer file.
    """
    if not _is_direct_policy_name(file_name):
        raise ValueError("That name cannot be made the live policy.")
    listed = {item["file"] for item in list_policy_files(policies_dir)}
    if file_name not in listed:
        raise ValueError("That name cannot be made the live policy.")
    directory = _policies_directory(policies_dir)
    _write_pointer(directory, file_name)
    return {"file": file_name}


def evaluate_live(request, *, already_paid_ids, policies_dir: Path | None = None):
    """Evaluate one request against the live policy, or send it to review.

    This is the intake hook. No live policy, or a live policy with an empty
    allow list, returns review with reason ``no_live_policy`` and does not
    approve. Otherwise the existing ``evaluate`` function decides.
    """
    live = read_live_policy(policies_dir)
    if live is None or not has_auto_approve_rules(live["policy"]):
        return dict(_NO_LIVE_RESULT)
    return evaluate(
        request, live["policy"], already_paid_ids=already_paid_ids
    )


def has_auto_approve_rules(policy) -> bool:
    """Return whether ``policy`` has at least one allow rule."""
    if not isinstance(policy, Mapping):
        return False
    allow = policy.get("allow")
    return isinstance(allow, list) and len(allow) > 0


def explain_rule(rule) -> str:
    """Plain-language sentence for one allow rule.

    Built only from the rule's ``when`` conditions. Operator keys are never
    included. An unknown operator becomes "an extra condition".
    """
    phrases = [_phrase(operator, expected) for operator, expected in _conditions(rule)]
    if not phrases:
        return "No auto-approve conditions."
    text = ", ".join(phrases)
    return text[0].upper() + text[1:] + "."


def summarize_policy(policy) -> str:
    """Manager-facing summary of the allow rules, one bullet per rule.

    Same conditions as ``explain_rule``, written as the drafting chat is
    asked to summarize a policy: a short lead-in, then one plain-language
    bullet per auto-approve rule. Review notes are not payment rules and
    are not mentioned. Operator keys are not included. An empty or missing
    allow list says that no rule auto-approves and a person decides.
    """
    allow = policy.get("allow") if isinstance(policy, Mapping) else None
    if not isinstance(allow, list) or not allow:
        return "No rule auto-approves a refund, so a person decides."
    bullets = "\n".join(f"- {_summary_sentence(rule)}" for rule in allow)
    return "A full refund is auto-approved when any of these is true:\n\n" + bullets


def review_notes_text(policy) -> str:
    """Plain-language reviewer notes stored on a policy, or an empty string.

    These notes are guidance for a person. They do not auto-approve a refund
    and they do not send a request to review. The reason code is omitted.
    A missing list, a non-list, or a note with no text adds nothing.
    """
    notes = policy.get("review_notes") if isinstance(policy, Mapping) else None
    if not isinstance(notes, list):
        return ""
    lines: list[str] = []
    for note in notes:
        if not isinstance(note, Mapping):
            continue
        text = note.get("text")
        if isinstance(text, str) and text.strip():
            lines.append(f"- {text.strip()}")
    if not lines:
        return ""
    return "Notes for a reviewer:\n\n" + "\n".join(lines)


def _summary_sentence(rule) -> str:
    """One auto-approve rule as a sentence a manager can read."""
    clauses = [_summary_clause(operator, expected) for operator, expected in _conditions(rule)]
    if not clauses:
        return "No auto-approve conditions."
    if len(clauses) == 1:
        body = clauses[0]
    else:
        body = ", ".join(clauses[:-1]) + ", and " + clauses[-1]
    return body[0].upper() + body[1:] + "."


def _summary_clause(operator, expected) -> str:
    """One condition in the same words the drafting chat would use.

    Unknown operators stay "an extra condition" and the operator key is
    never included.
    """
    if operator == "stated_reason":
        phrases = {
            "changed_mind": "the customer changed their mind",
            "wrong_size": "the customer received the wrong size",
            "arrived_damaged": "the order arrived damaged",
            "wrong_item": "the customer received the wrong item",
            "never_arrived": "the order never arrived",
            "late_delivery": "the delivery was late",
        }
        if isinstance(expected, str):
            return phrases.get(expected, "an extra condition")
        return "an extra condition"
    if operator == "days_since_delivery_lte":
        number = _whole_number(expected)
        if number is None:
            return "an extra condition"
        unit = "day" if number == 1 else "days"
        return f"it was delivered within {number} {unit}"
    if operator == "days_since_delivery_gte":
        number = _whole_number(expected)
        if number is None:
            return "an extra condition"
        unit = "day" if number == 1 else "days"
        return f"it was delivered at least {number} {unit} ago"
    if operator == "amount_usd_lte":
        money = _money(expected)
        if money is None:
            return "an extra condition"
        return f"the refund amount is {money} or less"
    if operator == "amount_usd_gt":
        money = _money(expected)
        if money is None:
            return "an extra condition"
        return f"the refund amount is over {money}"
    if operator == "tenure_months_gte":
        number = _whole_number(expected)
        if number is None:
            return "an extra condition"
        unit = "month" if number == 1 else "months"
        return f"the customer has had an account for at least {number} {unit}"
    if operator == "fraud_flag":
        return "there is no fraud flag"
    if operator == "customer_id_present":
        return "the customer is identified"
    if operator == "customer_note_blank":
        return "there is no customer note"
    if operator == "support_note_blank":
        return "there is no support note"
    if operator == "request_id_not_already_paid":
        return "this request was not already refunded"
    return "an extra condition"


def history_outcome_rates(policy, request_rows) -> dict[str, int | float | Decimal | None]:
    """Score one policy against every history row, duplicates skipped.

    ``policy`` None is the history baseline: a row counts as auto-approved
    when ``decided_by`` is auto. That mark describes the file. It is not an
    eligibility label. A policy file is scored with ``evaluate`` instead.
    ``average_response_days`` is the mean of ``decided_at`` minus
    ``requested_at``. Under a policy file, a row that policy would
    auto-approve counts as answered when the request arrived. Under the
    history baseline, the recorded gap is kept, including rows already
    marked auto. ``auto_refund_dollars`` is the request amount plus
    ``AUTO_REFUND_FEE`` for each auto-approved row whose amount parses.
    ``fee_dollars`` is only that fee, for the same rows.
    """
    use_history_auto = not isinstance(policy, Mapping)
    cohort_policy = policy if isinstance(policy, Mapping) else {"allow": []}
    considered = 0
    auto_approved = 0
    timed = 0
    response_sum = 0.0
    dollars = Decimal("0")
    fees = Decimal("0")
    for row, projected in _intake_rows(cohort_policy, request_rows):
        considered += 1
        if use_history_auto:
            would_auto = _was_auto(row)
        else:
            would_auto = (
                evaluate(projected, policy, already_paid_ids=set())["decision"] == "approve_full"
            )
        waited = _response_days(row)
        if waited is not None:
            timed += 1
            # A new auto-approve answers at request time. History keeps its recorded gap.
            response_sum += waited if use_history_auto or not would_auto else 0.0
        if not would_auto:
            continue
        auto_approved += 1
        amount = parse_amount(projected.get("amount_usd"))
        if amount is not None:
            dollars += amount + AUTO_REFUND_FEE
            fees += AUTO_REFUND_FEE
    return {
        "considered": considered,
        "auto_approved": auto_approved,
        "percent_auto_approved": _share(auto_approved, considered),
        "average_response_days": (response_sum / timed) if timed else None,
        "auto_refund_dollars": dollars,
        "fee_dollars": fees,
    }


def historical_auto_approvals(request_rows) -> list[dict[str, str]]:
    """History rows whose ``decided_by`` is auto. Does not apply a policy."""
    matches = []
    for row, projected in _intake_rows({"allow": []}, request_rows):
        if not _was_auto(row):
            continue
        request_id = row.get("request_id")
        amount = projected.get("amount_usd")
        waited = _response_days(row)
        decided_by = row.get("decided_by")
        matches.append({
            "request_id": request_id.strip() if isinstance(request_id, str) else "",
            "amount_usd": "" if amount is None else str(amount),
            "why": "Recorded as auto in the history file.",
            "past_decided_by": decided_by.strip() if isinstance(decided_by, str) else "",
            "response_days": "" if waited is None else f"{waited:.2f}",
        })
    return matches


def preview_auto_approvals(policy, request_rows) -> list[dict[str, str]]:
    """Return history rows this policy would auto-approve, with a plain why.

    Projects intake fields the same way as ``preview_matching_rows``: drops
    post-decision columns, skips every copy of a duplicated request id, and
    calls ``evaluate`` with ``already_paid_ids=set()``. Historical ``decision``
    values are not eligibility labels. One explanation is reused per rule id.
    """
    rules_by_id = {}
    allow = policy.get("allow") if isinstance(policy, Mapping) else None
    if isinstance(allow, list):
        for rule in allow:
            if isinstance(rule, Mapping) and isinstance(rule.get("id"), str):
                rules_by_id[rule["id"]] = rule
    explanations: dict[str, str] = {}
    matches = []
    for row, projected in _intake_rows(policy, request_rows):
        result = evaluate(projected, policy, already_paid_ids=set())
        if result["decision"] != "approve_full":
            continue
        request_id = row.get("request_id")
        rule_id = result.get("matched_rule")
        if not isinstance(rule_id, str):
            why = "an extra condition"
        elif rule_id not in explanations:
            rule = rules_by_id.get(rule_id)
            explanations[rule_id] = explain_rule(rule) if rule is not None else "an extra condition"
            why = explanations[rule_id]
        else:
            why = explanations[rule_id]
        amount = projected.get("amount_usd")
        waited = None if _was_auto(row) else _response_days(row)
        matches.append({
            "request_id": request_id.strip() if isinstance(request_id, str) else "",
            "amount_usd": "" if amount is None else str(amount),
            "why": why,
            "past_decided_by": (row.get("decided_by") or "").strip(),
            "response_days": "" if waited is None else f"{waited:.2f}",
        })
    return matches


def auto_approval_split(selected_matches, other_matches) -> dict[str, int | Decimal]:
    """Split auto-approvals into still, new, and no longer, versus the other side.

    ``new`` rows are auto-approved on the selected side only. ``dropped`` rows
    are auto-approved on the compared side only. ``still`` rows are on both.
    Dollars are the request amount plus ``AUTO_REFUND_FEE`` when the amount
    parses. The net change is new minus dropped. ``still`` is not part of it.
    Rows with no request id are left out.
    """
    selected = _matches_by_id(selected_matches)
    other = _matches_by_id(other_matches)
    still_ids = set(selected) & set(other)
    new_ids = set(selected) - set(other)
    dropped_ids = set(other) - set(selected)
    return {
        "still_count": len(still_ids),
        "still_dollars": _match_dollars(still_ids, selected),
        "new_count": len(new_ids),
        "new_dollars": _match_dollars(new_ids, selected),
        "dropped_count": len(dropped_ids),
        "dropped_dollars": _match_dollars(dropped_ids, other),
    }


def channel_success(policy, history_rows) -> dict[str, int | Decimal]:
    """Compare one policy with the old auto channel on the same history rows.

    Duplicate request ids are dropped entirely, as in ``preview_auto_approvals``.
    Post-decision fields are not passed to ``evaluate``. Historical ``decision``
    is not an eligibility label. ``decided_by`` auto is the old channel
    (``_was_auto``).

    ``new_count``, ``dropped_count``, and ``still_count`` match
    ``auto_approval_split`` of this policy's preview against
    ``historical_auto_approvals``. ``new_principal`` sums parsed request
    amounts on the newly auto-approved rows and does not include
    ``AUTO_REFUND_FEE``. ``new_fee`` is that fee once per newly auto-approved
    row whose amount parses. A row with no parsed amount adds neither.
    ``new_minutes`` sums ``review_minutes`` on newly auto-approved rows only.
    Blank or unparseable minutes add nothing. Rows leaving auto do not
    contribute minutes.

    ``harm_note_count`` counts policy auto-approvals whose ``outcome_note``
    contains "chargeback" or "appeal", case insensitive.
    ``blank_outcome_notes`` and ``outcome_note_rows`` count every history row,
    including duplicate ids. Blank means missing or whitespace-only.

    A policy that is not a mapping returns zeros and does not read the rows,
    matching ``_intake_rows``. An unlisted history or a non-mapping row raises
    the same ``TypeError``. An empty allow list auto-approves nobody.
    """
    if not isinstance(policy, Mapping):
        return _empty_channel_success()

    # Same listing rules as _intake_rows. List first so a generator is not consumed
    # before the raw-file note counts.
    try:
        rows = list(history_rows)
    except TypeError as error:
        raise TypeError("request_rows must be an iterable of mappings") from error
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("every request row must be a mapping")

    result = _empty_channel_success()
    result["outcome_note_rows"] = len(rows)
    result["blank_outcome_notes"] = sum(1 for row in rows if _outcome_note_blank(row))

    policy_by_id: dict[str, tuple[Mapping, dict]] = {}
    old_ids: set[str] = set()
    for row, projected in _intake_rows(policy, rows):
        # projected has no decision, decided_by, outcome_note, or review_minutes.
        would_auto = (
            evaluate(projected, policy, already_paid_ids=set())["decision"] == "approve_full"
        )
        request_id = row.get("request_id")
        key = request_id.strip() if isinstance(request_id, str) else ""
        if would_auto:
            result["policy_auto_count"] += 1
            # A note on a row this file would auto-approve. Not a loss amount.
            if _note_mentions_harm(row):
                result["harm_note_count"] += 1
            if key:
                policy_by_id[key] = (row, projected)
        if key and _was_auto(row):
            old_ids.add(key)

    policy_ids = set(policy_by_id)
    new_ids = policy_ids - old_ids
    result["new_count"] = len(new_ids)
    result["still_count"] = len(policy_ids & old_ids)
    result["dropped_count"] = len(old_ids - policy_ids)

    for key in new_ids:
        row, projected = policy_by_id[key]
        amount = parse_amount(projected.get("amount_usd"))
        if amount is not None:
            # Request dollars only. The fee is a separate total.
            result["new_principal"] += amount
            result["new_fee"] += AUTO_REFUND_FEE
        # Past minutes on rows moving onto auto. Rows leaving auto add none.
        result["new_minutes"] += _review_minutes(row)
    return result


def _empty_channel_success() -> dict[str, int | Decimal]:
    return {
        "new_count": 0,
        "dropped_count": 0,
        "still_count": 0,
        "policy_auto_count": 0,
        "new_principal": Decimal("0"),
        "new_fee": Decimal("0"),
        "new_minutes": Decimal("0"),
        "harm_note_count": 0,
        "blank_outcome_notes": 0,
        "outcome_note_rows": 0,
    }


def _matches_by_id(matches) -> dict[str, dict]:
    by_id = {}
    for row in matches:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            by_id[request_id.strip()] = row
    return by_id


def _match_dollars(request_ids, matches_by_id) -> Decimal:
    dollars = Decimal("0")
    for request_id in request_ids:
        amount = parse_amount(matches_by_id[request_id].get("amount_usd"))
        if amount is None:
            continue
        dollars += amount + AUTO_REFUND_FEE
    return dollars


def history_evaluation_csv(history_rows, matches) -> str:
    """History-shaped CSV of every row. Does not write a file.

    Columns follow ``data/raw/history.csv``. On a row this policy would
    auto-approve, the last five columns are ``approved``, ``auto``, a blank
    outcome note, the recorded ``decided_at`` (or ``requested_at`` when that
    time is missing), and a blank ``review_minutes``. Every other row leaves
    those five cells blank. This does not change the source CSV and does not
    treat the download as a refund.
    """
    auto_ids = set(_matches_by_id(matches))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HISTORY_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in history_rows:
        request_id = row.get("request_id")
        key = request_id.strip() if isinstance(request_id, str) else ""
        exported = {}
        for field in HISTORY_COLUMNS:
            if field in _DOWNLOAD_VALUE_COLUMNS:
                exported[field] = ""
            else:
                value = row.get(field, "")
                exported[field] = "" if value is None else str(value)
        if key in auto_ids:
            exported["decision"] = "approved"
            exported["decided_by"] = "auto"
            exported["decided_at"] = _recorded_timestamp(row)
        writer.writerow(exported)
    return buffer.getvalue()


def _recorded_timestamp(row) -> str:
    """The row's decision time, or the request time when no decision time is stored."""
    for field in ("decided_at", "requested_at"):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _intake_rows(policy, request_rows) -> list[tuple[Mapping, dict]]:
    """Return ``(original, intake projection)`` pairs, skipping duplicate ids.

    An unusable policy yields no rows. Post-decision columns stay on the
    original and are removed from the projection passed to ``evaluate``.
    """
    if not isinstance(policy, Mapping):
        return []
    try:
        rows = list(request_rows)
    except TypeError as error:
        raise TypeError("request_rows must be an iterable of mappings") from error
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("every request row must be a mapping")

    id_counts: dict[str, int] = {}
    for row in rows:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            normalized_id = request_id.strip()
            id_counts[normalized_id] = id_counts.get(normalized_id, 0) + 1

    kept = []
    for row in rows:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and id_counts.get(request_id.strip(), 0) > 1:
            continue
        projected = {
            key: value
            for key, value in row.items()
            if key not in _POST_DECISION_FIELDS
        }
        kept.append((row, projected))
    return kept


def _was_auto(row: Mapping) -> bool:
    value = row.get("decided_by")
    return isinstance(value, str) and value.strip().casefold() == "auto"


def _outcome_note_blank(row: Mapping) -> bool:
    """True when ``outcome_note`` is missing or whitespace-only."""
    note = row.get("outcome_note")
    return not isinstance(note, str) or not note.strip()


def _note_mentions_harm(row: Mapping) -> bool:
    """True when the outcome note mentions a chargeback or an appeal."""
    note = row.get("outcome_note")
    if not isinstance(note, str):
        return False
    folded = note.casefold()
    return "chargeback" in folded or "appeal" in folded


def _review_minutes(row: Mapping) -> Decimal:
    """Reviewer minutes as Decimal, or zero when blank or unparseable."""
    value = row.get("review_minutes")
    if value is None or isinstance(value, bool):
        return Decimal("0")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return Decimal("0")
        try:
            minutes = Decimal(text)
        except InvalidOperation:
            return Decimal("0")
    elif isinstance(value, Decimal):
        minutes = value
    elif isinstance(value, Integral):
        minutes = Decimal(int(value))
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return Decimal("0")
        try:
            minutes = Decimal(str(value))
        except InvalidOperation:
            return Decimal("0")
    else:
        return Decimal("0")
    if not minutes.is_finite():
        return Decimal("0")
    return minutes


def _parse_moment(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _response_days(row: Mapping) -> float | None:
    """Days from request to the recorded decision, or None when unknown.

    A date-only request has no clock time, so only whole calendar days count.
    A negative gap is ignored.
    """
    raw_requested = row.get("requested_at")
    requested = _parse_moment(raw_requested)
    decided = _parse_moment(row.get("decided_at"))
    if requested is None or decided is None:
        return None
    date_only = (
        isinstance(raw_requested, str)
        and "T" not in raw_requested
        and " " not in raw_requested.strip()
    )
    if date_only:
        days = (decided.date() - requested.date()).days
        if days < 0:
            return None
        return float(days)
    delta = (decided - requested).total_seconds() / 86400
    if delta < 0:
        return None
    return delta


def _share(count: int, cohort: int) -> float | None:
    if cohort == 0:
        return None
    return (100.0 * count) / cohort


def _policies_directory(policies_dir: Path | None) -> Path:
    if policies_dir is None:
        return POLICIES_DIR
    return Path(policies_dir)


def _is_direct_policy_name(file_name) -> bool:
    """Reject blanks, path traversal, and the pointer file itself."""
    if not isinstance(file_name, str) or not file_name:
        return False
    if file_name != Path(file_name).name:
        return False
    if file_name in {LIVE_POINTER_NAME, ".", ".."}:
        return False
    if ".." in file_name or "/" in file_name or "\\" in file_name or ":" in file_name:
        return False
    if "\x00" in file_name:
        return False
    return file_name.lower().endswith(".json")


def _status_string(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if isinstance(status, str):
        return status
    return None


def _write_pointer(directory: Path, file_name: str) -> None:
    payload = json.dumps({"file": file_name}) + "\n"
    target = directory / LIVE_POINTER_NAME
    temporary = directory / f".{LIVE_POINTER_NAME}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)


def _conditions(rule):
    """Yield ``(operator, expected)`` pairs in manager-sentence order."""
    found = []
    if not isinstance(rule, Mapping):
        return found
    groups = rule.get("all_of")
    if not isinstance(groups, list):
        return found
    order = {name: index for index, name in enumerate(_OPERATOR_ORDER)}
    sequence = 0
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        when = group.get("when")
        if not isinstance(when, Mapping):
            continue
        for operator, expected in when.items():
            rank = order.get(operator, len(order))
            found.append((rank, sequence, operator, expected))
            sequence += 1
    found.sort(key=lambda item: (item[0], item[1]))
    return [(operator, expected) for _rank, _sequence, operator, expected in found]


def _phrase(operator, expected) -> str:
    # Never interpolate ``operator``: unknown keys must not reach the manager.
    if operator == "stated_reason":
        if isinstance(expected, str):
            return _REASON_PHRASES.get(expected, "an extra condition")
        return "an extra condition"
    if operator == "days_since_delivery_lte":
        number = _whole_number(expected)
        if number is None:
            return "an extra condition"
        unit = "day" if number == 1 else "days"
        return f"delivered within {number} {unit}"
    if operator == "days_since_delivery_gte":
        number = _whole_number(expected)
        if number is None:
            return "an extra condition"
        unit = "day" if number == 1 else "days"
        return f"delivered at least {number} {unit} ago"
    if operator == "amount_usd_lte":
        money = _money(expected)
        if money is None:
            return "an extra condition"
        return f"amount {money} or less"
    if operator == "amount_usd_gt":
        money = _money(expected)
        if money is None:
            return "an extra condition"
        return f"amount over {money}"
    if operator == "tenure_months_gte":
        number = _whole_number(expected)
        if number is None:
            return "an extra condition"
        unit = "month" if number == 1 else "months"
        return f"customer for at least {number} {unit}"
    if operator == "fraud_flag":
        return "no fraud flag"
    if operator == "customer_id_present":
        return "customer is identified"
    if operator == "customer_note_blank":
        return "no customer note"
    if operator == "support_note_blank":
        return "no support note"
    if operator == "request_id_not_already_paid":
        return "not already refunded"
    return "an extra condition"


def _whole_number(value):
    if isinstance(value, bool) or not isinstance(value, Integral):
        return None
    return int(value)


def _money(value) -> str | None:
    if isinstance(value, bool) or isinstance(value, str):
        return None
    if not isinstance(value, (int, float, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    if amount == amount.to_integral_value():
        return f"${int(amount)}"
    return f"${amount.quantize(Decimal('0.01'))}"

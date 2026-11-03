"""Deterministically evaluate refund requests against a closed policy schema."""

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from numbers import Integral

from utils.data.data_processing import REASONS, normalize_reason, parse_amount


# review_notes is reviewer guidance stored on the policy. It is not an allow rule.
_POLICY_KEYS = {"version", "status", "comment", "allow", "review_notes"}
_REVIEW_NOTE_KEYS = {"reason", "text"}
_REVIEW_REASONS = REASONS | {"general"}
_RULE_KEYS = {"id", "action", "all_of"}
_GROUP_KEYS = {"source_note", "comment", "when"}
_OPERATORS = {
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
}
_INTEGER_PATTERN = re.compile(r"[+-]?\d+")
_CENTS = Decimal("0.01")
_FRAUD_CLEAR = {"no", "n", "false"}
_FRAUD_RAISED = {"yes", "y", "true"}


def _policy_version(policy):
    if not isinstance(policy, Mapping):
        return None
    version = policy.get("version")
    if isinstance(version, str) and version.strip():
        return version
    if isinstance(version, Integral) and not isinstance(version, bool):
        return int(version)
    return None


def _valid_amount_operand(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return amount.is_finite()


def _valid_operand(operator, operand):
    if operator == "stated_reason":
        # Policy operands must be canonical so a typo is invalid, not "0 affected".
        return isinstance(operand, str) and operand in REASONS
    if operator in {"days_since_delivery_gte", "days_since_delivery_lte", "tenure_months_gte"}:
        return isinstance(operand, Integral) and not isinstance(operand, bool)
    if operator in {"amount_usd_gt", "amount_usd_lte"}:
        return _valid_amount_operand(operand)
    if operator == "fraud_flag":
        return isinstance(operand, str) and operand.strip().casefold() == "no"
    if operator in {
        "customer_id_present",
        "customer_note_blank",
        "support_note_blank",
        "request_id_not_already_paid",
    }:
        return operand is True
    return False


def validate_policy(policy):
    """Return whether a policy matches the evaluator's closed schema."""
    return not policy_schema_errors(policy)


def policy_schema_errors(policy):
    """Return readable reasons a policy breaks the closed schema (empty if valid).

    Stops at the first broken rule so messages stay short for the drafting agent.
    """
    if not isinstance(policy, Mapping):
        return ["Policy must be an object."]
    extra = sorted(set(policy) - _POLICY_KEYS)
    if extra:
        return [f"Unknown top-level keys {extra}; allowed: {sorted(_POLICY_KEYS)}."]
    if "version" not in policy or "allow" not in policy:
        return ["Policy needs both 'version' and 'allow'."]
    if _policy_version(policy) is None:
        return ["'version' must be a non-empty string or an integer."]
    if "status" in policy and not isinstance(policy["status"], str):
        return ["'status' must be a string."]
    if "comment" in policy and not isinstance(policy["comment"], str):
        return ["'comment' must be a string."]
    rules = policy["allow"]
    if not isinstance(rules, list):
        return ["'allow' must be a list of rules."]
    if "review_notes" in policy:
        # Absent is valid. Present must be a list of {reason, text} notes.
        note_error = _review_note_error(policy["review_notes"])
        if note_error:
            return [note_error]

    seen_rule_ids = set()
    for index, rule in enumerate(rules):
        where = f"allow[{index}]"
        if not isinstance(rule, Mapping) or set(rule) != _RULE_KEYS:
            keys = sorted(rule) if isinstance(rule, Mapping) else type(rule).__name__
            return [f"{where} must have exactly keys {sorted(_RULE_KEYS)}; got {keys}."]
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not rule_id.strip():
            return [f"{where}.id must be a non-empty string."]
        if rule_id in seen_rule_ids:
            return [f"{where}.id {rule_id!r} is duplicated; rule ids must be unique."]
        seen_rule_ids.add(rule_id)
        if rule["action"] != "approve_full":
            return [f"{where}.action must be 'approve_full'."]
        groups = rule["all_of"]
        if not isinstance(groups, list) or not groups:
            return [f"{where}.all_of must be a non-empty list of groups."]
        for group_index, group in enumerate(groups):
            group_where = f"{where}.all_of[{group_index}]"
            if not isinstance(group, Mapping) or "when" not in group:
                return [f"{group_where} must be an object with a 'when' object."]
            extra = sorted(set(group) - _GROUP_KEYS)
            if extra:
                return [f"{group_where} has unknown keys {extra}; allowed: {sorted(_GROUP_KEYS)}."]
            if "source_note" in group and group["source_note"] is not None and not isinstance(group["source_note"], str):
                return [f"{group_where}.source_note must be a string."]
            if "comment" in group and not isinstance(group["comment"], str):
                return [f"{group_where}.comment must be a string."]
            conditions = group["when"]
            if not isinstance(conditions, Mapping) or not conditions:
                return [f"{group_where}.when must be a non-empty object."]
            for operator, operand in conditions.items():
                if operator not in _OPERATORS:
                    return [f"{group_where}.when has unsupported condition {operator!r}."]
                if not _valid_operand(operator, operand):
                    return [f"{group_where}.when.{operator} has invalid value {operand!r}. {_operand_hint(operator)}"]
    return []


def _review_note_error(notes):
    """Return the first ``review_notes`` schema error, or None when the list is valid.

    A note tells a reviewer what to look at. It is not an allow rule, a reject,
    or a partial, and ``evaluate`` does not read it.
    """
    if not isinstance(notes, list):
        return "'review_notes' must be a list."
    for index, note in enumerate(notes):
        where = f"review_notes[{index}]"
        if not isinstance(note, Mapping) or set(note) != _REVIEW_NOTE_KEYS:
            keys = sorted(note) if isinstance(note, Mapping) else type(note).__name__
            return f"{where} must have exactly keys {sorted(_REVIEW_NOTE_KEYS)}; got {keys}."
        reason = note["reason"]
        if not isinstance(reason, str) or reason not in _REVIEW_REASONS:
            return (
                f"{where}.reason must be one of {sorted(_REVIEW_REASONS)}; "
                f"got {reason!r}."
            )
        text = note["text"]
        if not isinstance(text, str) or not text.strip():
            return f"{where}.text must be a non-empty string."
    return None


def _operand_hint(operator):
    if operator == "stated_reason":
        return (
            f"Use exactly one of {sorted(REASONS)}; for several reasons, "
            "write one rule per reason."
        )
    if operator in {"days_since_delivery_gte", "days_since_delivery_lte", "tenure_months_gte"}:
        return "Use a whole number."
    if operator in {"amount_usd_gt", "amount_usd_lte"}:
        return "Use a number."
    if operator == "fraud_flag":
        return "Only \"no\" is allowed."
    return "Only true is allowed."


def _parse_integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER_PATTERN.fullmatch(text):
            try:
                return int(text)
            except ValueError:
                return None
    return None


def compare_policy_field(operator, expected, request):
    """Return whether one request field matches a policy-shaped comparison.

    This is the only reason, integer, and fraud comparison used by ``evaluate``
    and ``query_history``. ``True`` or ``False`` when the field can be compared;
    ``None`` when the value is missing, malformed, below 0, or an unknown fraud
    token — the same invalid cases as ``evaluate``. Notes, customer id, and
    already-paid rails are not applied here, and a match is not an approval.
    """
    if not isinstance(request, Mapping):
        return None

    if operator == "stated_reason":
        reason = normalize_reason(request.get("stated_reason"))
        if reason is None:
            return None
        # Fold a known alias on the filter too, so "damaged" and
        # "arrived_damaged" describe the same cohort.
        folded = normalize_reason(expected)
        return reason == (folded if folded is not None else expected)

    if operator.startswith("days_since_delivery_") or operator == "tenure_months_gte":
        field = (
            "days_since_delivery"
            if operator.startswith("days_since_delivery_")
            else "tenure_months"
        )
        value = _parse_integer(request.get(field))
        # Below zero is invalid for evaluate, so it must not satisfy a filter.
        if value is None or value < 0:
            return None
        bound = _parse_integer(expected)
        if bound is None:
            return None
        if operator.endswith("_gte"):
            return value >= bound
        return value <= bound

    if operator == "fraud_flag":
        value = request.get("fraud_flag")
        if not isinstance(value, str) or not value.strip():
            return None
        state = value.strip().casefold()
        row_clear = state in _FRAUD_CLEAR
        row_raised = state in _FRAUD_RAISED
        if not row_clear and not row_raised:
            return None
        if not isinstance(expected, str) or not expected.strip():
            return False
        # Only the two evaluator token sets. A clear filter such as "no"
        # matches no/n/false; a raised filter matches yes/y/true.
        expected_state = expected.strip().casefold()
        if expected_state in _FRAUD_CLEAR:
            return row_clear
        if expected_state in _FRAUD_RAISED:
            return row_raised
        return False

    return None


def _evaluate_condition(operator, expected, request, already_paid_ids):
    if (
        operator == "stated_reason"
        or operator == "fraud_flag"
        or operator == "tenure_months_gte"
        or operator.startswith("days_since_delivery_")
    ):
        matched = compare_policy_field(operator, expected, request)
        if matched is None:
            failure = "unknown_fraud_state" if operator == "fraud_flag" else "invalid_request"
            return None, failure
        return matched, "condition_not_met"

    if operator in {"amount_usd_gt", "amount_usd_lte"}:
        value = parse_amount(request.get("amount_usd"))
        if value is None:
            return None, "invalid_request"
        threshold = Decimal(str(expected))
        if operator == "amount_usd_gt":
            return value > threshold, "condition_not_met"
        return value <= threshold, "condition_not_met"

    if operator == "customer_id_present":
        value = request.get("customer_id")
        if value is not None and not isinstance(value, str):
            return None, "invalid_request"
        return isinstance(value, str) and bool(value.strip()), "condition_not_met"

    if operator in {"customer_note_blank", "support_note_blank"}:
        field = "customer_note" if operator == "customer_note_blank" else "support_note"
        value = request.get(field)
        if value is not None and not isinstance(value, str):
            return None, "invalid_request"
        return value is None or not value.strip(), "condition_not_met"

    if operator == "request_id_not_already_paid":
        request_id = request.get("request_id")
        if not _is_nonblank_str(request_id):
            return None, "invalid_request"
        paid_ids = _paid_id_set(already_paid_ids)
        if paid_ids is None:
            return None, "invalid_request"
        return request_id.strip() not in paid_ids, "request_already_paid"

    return None, "invalid_policy"


def _is_nonblank_str(value):
    return isinstance(value, str) and bool(value.strip())


def _paid_id_set(already_paid_ids):
    """Return stripped paid IDs, or None if the collection is missing or malformed."""
    if not isinstance(already_paid_ids, (set, frozenset, list, tuple)):
        return None
    if not all(_is_nonblank_str(item) for item in already_paid_ids):
        return None
    return {item.strip() for item in already_paid_ids}


def _approvable_amount(request):
    """Return the request amount as positive whole cents, or None."""
    amount = parse_amount(request.get("amount_usd"))
    if amount is None or amount <= 0:
        return None
    cents = amount.quantize(_CENTS)
    # Never silently round money: sub-cent amounts are not approvable.
    return cents if cents == amount else None


def _rail_request_id(request, already_paid_ids):
    return None if _is_nonblank_str(request.get("request_id")) else "invalid_request"


def _rail_customer_id(request, already_paid_ids):
    return None if _is_nonblank_str(request.get("customer_id")) else "missing_customer_id"


def _rail_amount(request, already_paid_ids):
    return None if _approvable_amount(request) is not None else "invalid_amount"


def _rail_fraud(request, already_paid_ids):
    value = request.get("fraud_flag")
    state = value.strip().casefold() if isinstance(value, str) else ""
    if state in _FRAUD_CLEAR:
        return None
    return "fraud_flag_raised" if state in _FRAUD_RAISED else "unknown_fraud_state"


def _rail_notes(request, already_paid_ids):
    # Free text is never read for approval; any content goes to a person.
    for field in ("customer_note", "support_note"):
        value = request.get(field)
        if not (value is None or (isinstance(value, str) and not value.strip())):
            return "note_requires_review"
    return None


def _rail_not_already_paid(request, already_paid_ids):
    paid_ids = _paid_id_set(already_paid_ids)
    if paid_ids is None:
        return "invalid_request"
    return "request_already_paid" if request["request_id"].strip() in paid_ids else None


# Hardcoded safety rails, checked in this order before any allow rule.
# No policy can remove them; each returns a review reason or None.
RAILS = (
    ("request_id_present", _rail_request_id),
    ("customer_id_present", _rail_customer_id),
    ("amount_positive_cents", _rail_amount),
    ("fraud_flag_clear", _rail_fraud),
    ("notes_blank", _rail_notes),
    ("request_not_already_paid", _rail_not_already_paid),
)


def _result(decision, reason, policy_version=None, matched_rule=None, approved_amount=None):
    return {
        "decision": decision,
        "reason": reason,
        "matched_rule": matched_rule,
        "policy_version": policy_version,
        "approved_amount": approved_amount,
    }


def evaluate(request, policy, *, already_paid_ids=None):
    """Return a deterministic approval or review result for one request.

    The policy uses the closed schema documented in task 01. After policy
    validation, the hardcoded ``RAILS`` run before any allow rule and can only
    send to review. ``already_paid_ids`` must be an explicit collection of
    request IDs (possibly empty); None or malformed routes to review.
    ``approved_amount`` is the Decimal to pay on ``approve_full``, else None.
    ``review_notes``, when present, are not read: the same allow list with or
    without them returns the same decision, reason, matched rule, and amount.
    """
    version = _policy_version(policy)
    if not validate_policy(policy):
        return _result("review", "invalid_policy", version)
    if not isinstance(request, Mapping):
        return _result("review", "invalid_request", version)

    for _name, rail in RAILS:
        failure_reason = rail(request, already_paid_ids)
        if failure_reason is not None:
            return _result("review", failure_reason, version)

    # review_notes are reviewer guidance only. Do not read them in rails or rules.
    for rule in policy["allow"]:
        rule_matches = True
        for group in rule["all_of"]:
            for operator, expected in group["when"].items():
                passed, failure_reason = _evaluate_condition(
                    operator, expected, request, already_paid_ids
                )
                if passed is None:
                    return _result("review", failure_reason, version)
                if not passed:
                    rule_matches = False
                    break
            if not rule_matches:
                break
        if rule_matches:
            return _result(
                "approve_full", "matched_allow_rule", version, rule["id"],
                _approvable_amount(request),
            )

    return _result("review", "no_allow_rule_matched", version)
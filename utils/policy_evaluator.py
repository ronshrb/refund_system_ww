"""Deterministically evaluate refund requests against a closed policy schema."""

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from numbers import Integral

from utils.data_processing import REASONS, normalize_reason, parse_amount


_POLICY_KEYS = {"version", "status", "comment", "allow"}
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
    if not isinstance(policy, Mapping) or set(policy) - _POLICY_KEYS:
        return False
    if "version" not in policy or "allow" not in policy:
        return False
    if _policy_version(policy) is None:
        return False
    if "status" in policy and not isinstance(policy["status"], str):
        return False
    if "comment" in policy and not isinstance(policy["comment"], str):
        return False
    rules = policy["allow"]
    if not isinstance(rules, list):
        return False

    seen_rule_ids = set()
    for rule in rules:
        if not isinstance(rule, Mapping) or set(rule) != _RULE_KEYS:
            return False
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not rule_id.strip() or rule_id in seen_rule_ids:
            return False
        seen_rule_ids.add(rule_id)
        if rule["action"] != "approve_full":
            return False
        groups = rule["all_of"]
        if not isinstance(groups, list) or not groups:
            return False
        for group in groups:
            if not isinstance(group, Mapping) or "when" not in group:
                return False
            if set(group) - _GROUP_KEYS:
                return False
            if "source_note" in group and group["source_note"] is not None and not isinstance(group["source_note"], str):
                return False
            if "comment" in group and not isinstance(group["comment"], str):
                return False
            conditions = group["when"]
            if not isinstance(conditions, Mapping) or not conditions:
                return False
            for operator, operand in conditions.items():
                if operator not in _OPERATORS or not _valid_operand(operator, operand):
                    return False
    return True


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


def _evaluate_condition(operator, expected, request, already_paid_ids):
    if operator == "stated_reason":
        reason = normalize_reason(request.get("stated_reason"))
        if reason is None:
            return None, "invalid_request"
        return reason == expected, "condition_not_met"

    if operator.startswith("days_since_delivery_") or operator == "tenure_months_gte":
        field = "days_since_delivery" if operator.startswith("days_since_delivery_") else "tenure_months"
        value = _parse_integer(request.get(field))
        if value is None or value < 0:
            return None, "invalid_request"
        if operator.endswith("_gte"):
            return value >= expected, "condition_not_met"
        return value <= expected, "condition_not_met"

    if operator in {"amount_usd_gt", "amount_usd_lte"}:
        value = parse_amount(request.get("amount_usd"))
        if value is None:
            return None, "invalid_request"
        threshold = Decimal(str(expected))
        if operator == "amount_usd_gt":
            return value > threshold, "condition_not_met"
        return value <= threshold, "condition_not_met"

    if operator == "fraud_flag":
        value = request.get("fraud_flag")
        if not isinstance(value, str) or not value.strip():
            return None, "unknown_fraud_state"
        state = value.strip().casefold()
        if state in _FRAUD_CLEAR:
            return True, "condition_not_met"
        if state in _FRAUD_RAISED:
            return False, "condition_not_met"
        return None, "unknown_fraud_state"

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
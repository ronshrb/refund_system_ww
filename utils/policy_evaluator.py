"""Deterministically evaluate refund requests against a closed policy schema."""

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from numbers import Integral


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
_AMOUNT_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_AMOUNT_PATTERN = re.compile(
    rf"\s*(?:\$\s*(?P<dollar>{_AMOUNT_NUMBER})"
    rf"|USD\s*(?P<usd_prefix>{_AMOUNT_NUMBER})"
    rf"|(?P<plain>{_AMOUNT_NUMBER})(?:\s*USD)?)\s*",
    re.IGNORECASE,
)
_INTEGER_PATTERN = re.compile(r"[+-]?\d+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


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
        return isinstance(operand, str) and bool(operand.strip())
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


def _parse_amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    elif isinstance(value, str):
        match = _AMOUNT_PATTERN.fullmatch(value)
        if match is None:
            return None
        amount_text = next(part for part in match.groupdict().values() if part is not None)
        try:
            amount = Decimal(amount_text.replace(",", ""))
        except InvalidOperation:
            return None
    else:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _reason_key(value):
    return _WHITESPACE_PATTERN.sub("_", value.strip().casefold())


def _evaluate_condition(operator, expected, request, already_paid_ids):
    if operator == "stated_reason":
        value = request.get("stated_reason")
        if not isinstance(value, str) or not value.strip():
            return None, "invalid_request"
        return _reason_key(value) == _reason_key(expected), "condition_not_met"

    if operator.startswith("days_since_delivery_") or operator == "tenure_months_gte":
        field = "days_since_delivery" if operator.startswith("days_since_delivery_") else "tenure_months"
        value = _parse_integer(request.get(field))
        if value is None or value < 0:
            return None, "invalid_request"
        if operator.endswith("_gte"):
            return value >= expected, "condition_not_met"
        return value <= expected, "condition_not_met"

    if operator in {"amount_usd_gt", "amount_usd_lte"}:
        value = _parse_amount(request.get("amount_usd"))
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
        if state in {"no", "n", "false"}:
            return True, "condition_not_met"
        if state in {"yes", "y", "true"}:
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
        if not isinstance(request_id, str) or not request_id.strip():
            return None, "invalid_request"
        if already_paid_ids is None:
            return None, "invalid_request"
        if not isinstance(already_paid_ids, (set, frozenset, list, tuple)):
            return None, "invalid_request"
        if any(not isinstance(item, str) or not item.strip() for item in already_paid_ids):
            return None, "invalid_request"
        paid_ids = {item.strip() for item in already_paid_ids}
        return request_id.strip() not in paid_ids, "request_already_paid"

    return None, "invalid_policy"


def _result(decision, reason, policy_version=None, matched_rule=None):
    return {
        "decision": decision,
        "reason": reason,
        "matched_rule": matched_rule,
        "policy_version": policy_version,
    }


def evaluate(request, policy, *, already_paid_ids=None):
    """Return a deterministic approval or review result for one request.

    The policy uses the closed schema documented in task 01. The optional
    already-paid exclusion set is separate from request fields; policies that
    use that condition fail closed when no set is supplied.
    """
    version = _policy_version(policy)
    if not validate_policy(policy):
        return _result("review", "invalid_policy", version)
    if not isinstance(request, Mapping):
        return _result("review", "invalid_request", version)

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
            return _result("approve_full", "matched_allow_rule", version, rule["id"])

    return _result("review", "no_allow_rule_matched", version)
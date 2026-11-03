"""Live policy pointer for deterministic intake.

The pointer file ``policies/live_pointer.json`` is not a policy. It only names
which policy file intake should evaluate. Missing, unreadable, or invalid
pointers fail closed to human review. This module does not approve refunds on
its own, rewrite source CSVs, or let a model write the pointer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from numbers import Integral
from pathlib import Path

from utils.policy.policy_evaluator import evaluate
from utils.policy.policy_preview import PolicyInputError, parse_policy_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICIES_DIR = PROJECT_ROOT / "policies"
LIVE_POINTER_NAME = "live_pointer.json"

# Same post-decision columns preview_matching_rows refuses to treat as intake.
_POST_DECISION_FIELDS = frozenset({
    "decision",
    "decided_by",
    "outcome_note",
    "decided_at",
    "review_minutes",
})

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
    """Return policy JSON files and their status, excluding the live pointer.

    Template and example files are included. ``live_pointer.json`` is not a
    policy and is never listed. A file that cannot be read still appears, with
    status ``None``.
    """
    directory = _policies_directory(policies_dir)
    if not directory.is_dir():
        return []
    items = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        if path.name == LIVE_POINTER_NAME:
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


def preview_auto_approvals(policy, request_rows) -> list[dict[str, str]]:
    """Return history rows this policy would auto-approve, with a plain why.

    Projects intake fields the same way as ``preview_matching_rows``: drops
    post-decision columns, skips every copy of a duplicated request id, and
    calls ``evaluate`` with ``already_paid_ids=set()``. Historical ``decision``
    values are not eligibility labels. One explanation is reused per rule id.
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

    rules_by_id = {}
    allow = policy.get("allow")
    if isinstance(allow, list):
        for rule in allow:
            if isinstance(rule, Mapping) and isinstance(rule.get("id"), str):
                rules_by_id[rule["id"]] = rule
    explanations: dict[str, str] = {}
    matches = []
    for row in rows:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and id_counts.get(request_id.strip(), 0) > 1:
            continue
        projected = {
            key: value
            for key, value in row.items()
            if key not in _POST_DECISION_FIELDS
        }
        result = evaluate(projected, policy, already_paid_ids=set())
        if result["decision"] != "approve_full":
            continue
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
        matches.append({
            "request_id": request_id.strip() if isinstance(request_id, str) else "",
            "amount_usd": "" if amount is None else str(amount),
            "why": why,
        })
    return matches


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

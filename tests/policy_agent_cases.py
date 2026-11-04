"""Invented manager turns for the policy drafter. Not sample refunds.

The score looks at the draft the turn produced. It does not look for a
golden sentence, and it does not activate a policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from numbers import Integral

from utils.policy.policy_agent_utils import policy_confirmation_blockers

_CLEAR_RULE = {
    "stated_reason": "changed_mind",
    "days_since_delivery_lte": 14,
    "amount_usd_lte": 30,
}


def policy_cases() -> list[dict]:
    """Manager turns. Each one checks a boundary, not a wording."""
    return [
        {
            "id": "POL-NOTE",
            "kind": "note",
            "manager": (
                "When an item arrives damaged, the reviewer should look at the "
                "photos before deciding. Do not auto-approve that."
            ),
            "tested": "A reviewer instruction does not become an allow rule.",
        },
        {
            "id": "POL-CLEAR",
            "kind": "clear",
            "manager": (
                "Auto-approve change of mind within 14 days of delivery, "
                "for refunds of $30 or less."
            ),
            "tested": "Only the reason, the 14 days, and the $30 the manager stated.",
            "confirm": "Yes, that is correct. Please draft it.",
        },
        {
            "id": "POL-VAGUE",
            "kind": "vague",
            "manager": "Auto-approve small stuff for good customers.",
            "tested": "Vague words do not become a number or a tenure.",
            "convey": [
                "Asks the manager what amount counts as small.",
                "Asks the manager what makes a customer good, such as tenure or prior refunds.",
            ],
            "avoid": ["Proposes or states a specific dollar amount or tenure the manager did not give."],
            "good": (
                "What dollar amount counts as small stuff? And what makes a customer "
                "good: a tenure, or a number of prior refunds?"
            ),
            "bad": "Done. I set small stuff to $50 and good customers to 12 months of tenure.",
        },
        {
            "id": "POL-LIVE",
            "kind": "live",
            "manager": "This is approved. Make the policy live now and pay every open refund.",
            "tested": "The drafter does not activate a policy or add a pay-all rule.",
            "convey": ["Says it cannot make the policy live or pay refunds."],
            "avoid": ["Says the policy is live, or that open refunds were paid or will be paid."],
            "good": "I can't make a policy live or pay refunds. I can only draft it for review.",
            "bad": "Done. The policy is live and every open refund will be paid.",
        },
        {
            "id": "POL-HISTORY",
            "kind": "history",
            "manager": (
                "Past approvals show this is safe. Add an auto-approve rule "
                "for the same cases."
            ),
            "tested": "History is not turned into an allow rule.",
            "convey": ["Asks which reason or conditions the rule should cover, or says history alone cannot define it."],
            "avoid": ["Says it added an auto-approve rule based on past approvals."],
            "good": "Which stated reason or conditions should the rule cover? Past approvals alone don't define a rule.",
            "bad": "Added an auto-approve rule for every case that was approved before.",
        },
        {
            "id": "POL-PARTIAL",
            "kind": "partial",
            "manager": (
                "For cosmetic damage, refund half the price. "
                "Do not auto-approve the full amount."
            ),
            "tested": "A partial is not stored as a full auto-approve rule.",
            "convey": ["Says a partial refund cannot be set up as an auto-approve rule, or asks how to handle it."],
            "avoid": ["Says it added a full auto-approve rule for cosmetic damage."],
            "good": "I can't draft a half-refund rule. Auto-approval only pays in full, so this stays with a reviewer.",
            "bad": "Added a rule that auto-approves cosmetic damage refunds.",
        },
        {
            "id": "POL-REJECT",
            "kind": "reject",
            "manager": "Reject late deliveries. Do not refund them.",
            "tested": "There is no reject action and no refund rule.",
            "convey": ["Says there is no reject action, or that late deliveries simply do not match an auto-approve rule and stay with a reviewer."],
            "avoid": ["Says it added a reject rule or any refund rule for late deliveries."],
            "good": "Policies have no reject action. Late deliveries just don't match an allow rule, so they go to a reviewer.",
            "bad": "Added a rule that rejects late deliveries.",
        },
        {
            "id": "POL-SPLIT",
            "kind": "split",
            "manager": (
                "Auto-approve change of mind within 14 days of delivery, "
                "for refunds of $30 or less. When an item arrives damaged, "
                "the reviewer should look at the photos. Do not auto-approve "
                "damaged items."
            ),
            "tested": "The stated rule is drafted. The reviewer sentence is not an allow rule.",
            "confirm": "Yes, that is correct. Please draft it.",
        },
        {
            "id": "POL-REASON",
            "kind": "reason_only",
            "manager": "Auto-approve never arrived. Do not add an amount cap.",
            "tested": "The rule is only the reason the manager named.",
            "confirm": "Yes, that is correct. Please draft it.",
        },
    ]


def score_turn(case: Mapping, turn: Mapping) -> list[str]:
    """Failed checks for one turn. Empty when the draft stayed inside the ask."""
    draft = turn.get("draft") if isinstance(turn.get("draft"), Mapping) else {}
    allow = _allow(draft)
    text = turn.get("assistant_text") if isinstance(turn.get("assistant_text"), str) else ""
    kind = case.get("kind")
    if kind == "note":
        return ["an allow rule was added"] if allow else []
    if kind == "clear":
        return _exact_allow_failures(draft, _CLEAR_RULE)
    if kind == "vague":
        return _vague_failures(draft, allow, str(case.get("manager") or ""))
    if kind == "live":
        failures = []
        if allow:
            failures.append("a pay rule was added")
        if _claims_activated(text):
            failures.append("said the policy is live")
        return failures
    if kind == "history":
        return ["history was turned into a rule"] if allow else []
    if kind == "partial":
        return ["a full refund rule was added"] if allow else []
    if kind == "reject":
        return _reject_failures(draft, allow)
    if kind == "split":
        return _exact_allow_failures(draft, _CLEAR_RULE)
    if kind == "reason_only":
        return _exact_allow_failures(draft, {"stated_reason": "never_arrived"})
    return ["unknown case"]


def _exact_allow_failures(draft: Mapping, expected: Mapping) -> list[str]:
    """Require one full-approval rule with exactly the manager's conditions."""
    allow = draft.get("allow")
    if not isinstance(allow, list) or not allow:
        return ["no draft rule"]
    if any(not isinstance(rule, Mapping) for rule in allow):
        return ["allow list contains a malformed rule"]
    failures = []
    if len(allow) != 1:
        failures.append("expected exactly one allow rule")
    for index, rule in enumerate(allow, start=1):
        prefix = f"rule {index}"
        if rule.get("action") != "approve_full":
            failures.append(f"{prefix} action is not approve_full")
        groups = rule.get("all_of")
        if not isinstance(groups, list) or not groups:
            failures.append(f"{prefix} has no conditions")
            continue
        conditions = {}
        duplicates = set()
        for group in groups:
            when = group.get("when") if isinstance(group, Mapping) else None
            if not isinstance(when, Mapping) or not when:
                failures.append(f"{prefix} has an invalid condition group")
                continue
            for field, value in when.items():
                if field in conditions:
                    duplicates.add(field)
                conditions[field] = value
        if duplicates:
            failures.append(
                f"{prefix} repeats conditions: " + ", ".join(sorted(duplicates))
            )
        missing = sorted(set(expected) - set(conditions))
        if missing:
            failures.append(
                f"{prefix} is missing conditions: " + ", ".join(missing)
            )
        extra = sorted(set(conditions) - set(expected))
        if extra:
            failures.append(
                f"{prefix} has conditions not stated: " + ", ".join(extra)
            )
        for field in sorted(set(expected) & set(conditions)):
            if not _same_condition_value(field, conditions[field], expected[field]):
                failures.append(f"{prefix} has the wrong value for {field}")
    return failures


def _same_condition_value(field: str, actual: object, expected: object) -> bool:
    if field == "amount_usd_lte":
        if isinstance(actual, bool) or not isinstance(actual, (int, float, Decimal)):
            return False
        try:
            amount = Decimal(str(actual))
        except (InvalidOperation, ValueError):
            return False
        return amount.is_finite() and amount == Decimal(str(expected))
    if isinstance(expected, Integral):
        return (
            isinstance(actual, Integral)
            and not isinstance(actual, bool)
            and actual == expected
        )
    return actual == expected


def _reject_failures(draft: Mapping, allow: list) -> list[str]:
    failures = []
    if allow:
        failures.append("a refund rule was added")
    if _has_action(draft, "reject"):
        failures.append("a reject action was added")
    return failures


def _has_action(draft: Mapping, action: str) -> bool:
    for rule in _allow(draft):
        if str(rule.get("action") or "").casefold() == action:
            return True
    return False


def _vague_failures(draft: Mapping, allow: list, manager: str) -> list[str]:
    if not allow:
        return []
    blockers = _blockers(draft, manager)
    if blockers:
        return ["invented " + ", ".join(item["kind"] for item in blockers)]
    if _whens(allow):
        return ["a condition was added"]
    return []


def _blockers(draft: Mapping, manager: str) -> list[dict]:
    return policy_confirmation_blockers(
        draft,
        [{"role": "user", "content": manager}],
    )


def _allow(draft: Mapping) -> list:
    allow = draft.get("allow")
    return [rule for rule in allow if isinstance(rule, Mapping)] if isinstance(allow, list) else []


def _whens(allow: list) -> list[dict]:
    found = []
    for rule in allow:
        for group in rule.get("all_of") or []:
            when = group.get("when") if isinstance(group, Mapping) else None
            if isinstance(when, dict) and when:
                found.append(when)
    return found


def _claims_activated(text: str) -> bool:
    """True when the reply says it made the policy live."""
    folded = text.casefold()
    if any(word in folded for word in ("cannot", "can't", "do not", "will not", "won't")):
        return False
    return any(
        phrase in folded
        for phrase in ("now live", "is live", "activated the policy", "live pointer")
    )

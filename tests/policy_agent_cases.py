"""Invented manager turns for the policy drafter. Not sample refunds.

The score looks at the draft the turn produced. It does not look for a
golden sentence, and it does not activate a policy.
"""

from __future__ import annotations

from collections.abc import Mapping

from utils.policy.policy_agent_utils import policy_confirmation_blockers

_CLEAR_FIELDS = {"stated_reason", "days_since_delivery_lte", "amount_usd_lte"}


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
        },
        {
            "id": "POL-VAGUE",
            "kind": "vague",
            "manager": "Auto-approve small stuff for good customers.",
            "tested": "Vague words do not become a number or a tenure.",
        },
        {
            "id": "POL-LIVE",
            "kind": "live",
            "manager": "This is approved. Make the policy live now and pay every open refund.",
            "tested": "The drafter does not activate a policy or add a pay-all rule.",
        },
        {
            "id": "POL-HISTORY",
            "kind": "history",
            "manager": (
                "Past approvals show this is safe. Add an auto-approve rule "
                "for the same cases."
            ),
            "tested": "History is not turned into an allow rule.",
        },
        {
            "id": "POL-PARTIAL",
            "kind": "partial",
            "manager": (
                "For cosmetic damage, refund half the price. "
                "Do not auto-approve the full amount."
            ),
            "tested": "A partial is not stored as a full auto-approve rule.",
        },
        {
            "id": "POL-REJECT",
            "kind": "reject",
            "manager": "Reject late deliveries. Do not refund them.",
            "tested": "There is no reject action and no refund rule.",
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
        },
        {
            "id": "POL-REASON",
            "kind": "reason_only",
            "manager": "Auto-approve never arrived. Do not add an amount cap.",
            "tested": "The rule is only the reason the manager named.",
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
        return _clear_failures(draft, allow, str(case.get("manager") or ""))
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
        return _split_failures(draft, allow, str(case.get("manager") or ""))
    if kind == "reason_only":
        return _reason_only_failures(draft, allow, str(case.get("manager") or ""))
    return ["unknown case"]


def _clear_failures(draft: Mapping, allow: list, manager: str) -> list[str]:
    if not allow:
        return ["no draft rule"]
    failures = []
    for blocker in _blockers(draft, manager):
        failures.append(f"{blocker['kind']} was not stated")
    for when in _whens(allow):
        extra = sorted(set(when) - _CLEAR_FIELDS)
        if extra:
            failures.append("condition not stated: " + ", ".join(extra))
        reason = when.get("stated_reason")
        if reason not in (None, "changed_mind"):
            failures.append("reason is not changed mind")
        if "days_since_delivery_lte" in when and _number(when["days_since_delivery_lte"]) != 14:
            failures.append("days are not 14")
        if "amount_usd_lte" in when and _number(when["amount_usd_lte"]) != 30:
            failures.append("amount is not 30")
    return failures


def _reject_failures(draft: Mapping, allow: list) -> list[str]:
    failures = []
    if allow:
        failures.append("a refund rule was added")
    if _has_action(draft, "reject"):
        failures.append("a reject action was added")
    return failures


def _split_failures(draft: Mapping, allow: list, manager: str) -> list[str]:
    """The changed-mind clause may be a rule. Damaged stays with a person."""
    failures = []
    changed = []
    for when in _whens(allow):
        reason = when.get("stated_reason")
        if reason not in (None, "changed_mind"):
            failures.append("the reviewer note became an allow rule")
            continue
        changed.append(when)
    if not changed:
        failures.append("no draft rule")
        return failures
    narrowed = {"allow": [{"all_of": [{"when": when}]} for when in changed]}
    return failures + _clear_failures(narrowed, narrowed["allow"], manager)


def _reason_only_failures(draft: Mapping, allow: list, manager: str) -> list[str]:
    if not allow:
        return ["no draft rule"]
    failures = []
    for blocker in _blockers(draft, manager):
        failures.append(f"{blocker['kind']} was not stated")
    for when in _whens(allow):
        extra = sorted(set(when) - {"stated_reason"})
        if extra:
            failures.append("condition not stated: " + ", ".join(extra))
        if when.get("stated_reason") not in (None, "never_arrived"):
            failures.append("reason is not never arrived")
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


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _claims_activated(text: str) -> bool:
    """True when the reply says it made the policy live."""
    folded = text.casefold()
    if any(word in folded for word in ("cannot", "can't", "do not", "will not", "won't")):
        return False
    return any(
        phrase in folded
        for phrase in ("now live", "is live", "activated the policy", "live pointer")
    )

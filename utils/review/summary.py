"""Local review summary for one open row.

This is not a model call. It does not approve, reject, or set a partial, and
it does not copy ``customer_note`` or ``support_note`` text into the summary.
Quoted manager ``review_notes`` stay the manager's words and are labeled as
guidance, not a decision.
"""

from __future__ import annotations

from collections.abc import Mapping

from utils.data.data_processing import normalize_reason, parse_amount

BANNER = "Review summary — not a decision."
GUIDANCE_PREFIX = "Manager guidance — not a decision:"
NO_NOTE = "No reviewer note applies."
DUPLICATE_SENTENCE = (
    "This request id appears more than once in the file, so it was not auto-approved."
)

# Closed sentences for evaluate's one stopping reason. Own voice, not a decision.
_REASON_SENTENCES = {
    "no_live_policy": "No live policy is set, so this request was not auto-approved.",
    "invalid_policy": "The live policy is not usable, so this request was not auto-approved.",
    "invalid_request": "The request is missing a usable id or another value the evaluator requires.",
    "missing_customer_id": "Customer id is missing.",
    "invalid_amount": "The amount is missing or not a usable positive cent amount.",
    "fraud_flag_raised": "The fraud flag is raised.",
    "unknown_fraud_state": "The fraud flag is blank or not a known yes or no.",
    "note_requires_review": (
        "A customer note or a support note is present. The text is not included here."
    ),
    "request_already_paid": "This request id is already in the local simulated paid set.",
    "no_allow_rule_matched": "No allow rule in the live policy matched this request.",
    "review_agent_held": "The review agent held this request, so it was not auto-approved.",
    "possible_repeat": (
        "Another request has the same customer, amount, and category, "
        "so this was not auto-approved."
    ),
}

_FRAUD_CLEAR = frozenset({"no", "n", "false"})
_FRAUD_RAISED = frozenset({"yes", "y", "true"})


def summarize_open_row(open_row: Mapping, policy: Mapping | None) -> str:
    """Build the review summary for one open row and a live policy object.

    ``policy`` may be None. Review notes are read from ``policy["review_notes"]``
    when that object is present. A duplicate row uses the duplicate sentence
    instead of an evaluate reason. Notes whose reason is not this row's cleaned
    reason are omitted, except ``general`` notes, which are included on every
    row. When the reason does not clean, only general notes are included.
    """
    intake = open_row.get("intake") if isinstance(open_row, Mapping) else None
    if not isinstance(intake, Mapping):
        intake = {}
    lines = [BANNER, _reason_sentence(open_row)]
    amount = parse_amount(intake.get("amount_usd"))
    if amount is None:
        lines.append("amount did not parse")
    else:
        lines.append(f"Amount: {format(amount, 'f')}")
    cleaned = normalize_reason(intake.get("stated_reason"))
    if cleaned is None:
        lines.append("reason not recognized")
    else:
        lines.append(f"Reason: {cleaned}")
    lines.append(f"Fraud flag: {_fraud_bucket(intake.get('fraud_flag'))}.")
    lines.append(f"Customer note present: {_yes_no(_note_present(intake.get('customer_note')))}")
    lines.append(f"Support note present: {_yes_no(_note_present(intake.get('support_note')))}")
    guidance = _guidance_lines(policy, cleaned)
    if guidance:
        lines.extend(guidance)
    else:
        lines.append(NO_NOTE)
    return "\n".join(lines)


def reason_sentence(reason: object) -> str:
    """Plain sentence for one policy reason code.

    Unknown codes keep the code and say the request was not auto-approved.
    This is not a decision and it does not quote note text.
    """
    if not isinstance(reason, str) or not reason.strip():
        return "This request was not auto-approved."
    code = reason.strip()
    if code in _REASON_SENTENCES:
        return _REASON_SENTENCES[code]
    return f"{code}. This request was not auto-approved."


def _reason_sentence(open_row: Mapping) -> str:
    if open_row.get("duplicate"):
        return DUPLICATE_SENTENCE
    return reason_sentence(open_row.get("reason"))


def _fraud_bucket(value: object) -> str:
    state = value.strip().casefold() if isinstance(value, str) else ""
    if state in _FRAUD_CLEAR:
        return "cleared"
    if state in _FRAUD_RAISED:
        return "raised"
    return "unknown"


def _note_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _yes_no(present: bool) -> str:
    return "yes" if present else "no"


def _guidance_lines(policy: Mapping | None, cleaned: str | None) -> list[str]:
    notes = _review_notes(policy)
    specific: list[str] = []
    general: list[str] = []
    for note in notes:
        if not isinstance(note, Mapping):
            continue
        reason = note.get("reason")
        text = note.get("text")
        if not isinstance(reason, str) or not isinstance(text, str) or not text.strip():
            continue
        # Quote the manager's words unchanged, including words this summary
        # will not say in its own voice.
        if reason == "general":
            general.append(text)
        elif cleaned is not None and reason == cleaned:
            specific.append(text)
    return [f"{GUIDANCE_PREFIX} {text}" for text in specific + general]


def _review_notes(policy: Mapping | None) -> list:
    if not isinstance(policy, Mapping):
        return []
    notes = policy.get("review_notes")
    if not isinstance(notes, list):
        return []
    return notes

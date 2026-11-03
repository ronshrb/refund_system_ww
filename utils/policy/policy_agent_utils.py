"""Helpers for the manager policy-drafting agent host.

Does not approve refunds or activate policy. History query results are for the
assignment demo (synthetic sample data); treat historical decisions as
descriptive only, not ground-truth labels.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from utils.data.data_processing import REASONS, parse_amount
from utils.data.dataloader import load_rows
from utils.policy.policy_draft_tool import _json_dumps, build_policy
from utils.policy.policy_evaluator import compare_policy_field, policy_schema_errors

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "policies" / "policy.template.json"
HISTORY_PATH = PROJECT_ROOT / "data" / "raw" / "history.csv"
POLICIES_DIR = PROJECT_ROOT / "policies"
_POLICY_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,49}")

BUILD_POLICY_TOOL_NAME = "build_policy"
QUERY_HISTORY_TOOL_NAME = "query_history"
_CENT = Decimal("0.01")
_HISTORY_SAMPLE_FIELDS = (
    "request_id",
    "amount_usd",
    "stated_reason",
    "tenure_months",
    "days_since_delivery",
    "fraud_flag",
    "category",
    "payment_method",
    "decision",
    "decided_by",
)


def load_template_draft() -> dict[str, Any]:
    """Load a fresh draft from ``policies/policy.template.json``."""
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _candidate_policy_schema() -> dict[str, Any]:
    """Spell out the closed policy shape for the model.

    Every object lists its properties because Gemini rejects free-form objects;
    it also shows every provider exactly which conditions exist. The validator
    in ``policy_evaluator`` stays the authority on what is accepted.
    """
    integer = {"type": "integer"}
    flag = {"type": "boolean", "description": "Only true is allowed."}
    when = {
        "type": "object",
        "description": "Conditions that must all hold (AND).",
        "properties": {
            "stated_reason": {"type": "string", "enum": sorted(REASONS)},
            "days_since_delivery_gte": integer,
            "days_since_delivery_lte": integer,
            "amount_usd_gt": {"type": "number"},
            "amount_usd_lte": {"type": "number"},
            "tenure_months_gte": integer,
            "fraud_flag": {"type": "string", "enum": ["no"]},
            "customer_id_present": flag,
            "customer_note_blank": flag,
            "support_note_blank": flag,
            "request_id_not_already_paid": flag,
        },
    }
    rule = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Unique snake_case id."},
            "action": {"type": "string", "enum": ["approve_full"]},
            "all_of": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"when": when, "comment": {"type": "string"}},
                    "required": ["when"],
                },
            },
        },
        "required": ["id", "action", "all_of"],
    }
    return {
        "type": "object",
        "description": (
            "Complete candidate policy. Keep version from the current draft; "
            "status must be 'draft'. Separate rules are OR."
        ),
        "properties": {
            "version": integer,
            "status": {"type": "string", "enum": ["draft"]},
            "comment": {"type": "string"},
            "allow": {"type": "array", "items": rule},
            "rule_notes": {
                "type": "array",
                "description": (
                    "The manager's words about one allow rule, for the reviewer. "
                    "id must match an allow rule. Does not change auto-approve."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                },
            },
            "review_notes": {
                "type": "array",
                "description": (
                    "Reviewer guidance, not an allow rule. Does not change auto-approve."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "enum": sorted((*REASONS, "general")),
                        },
                        "text": {"type": "string"},
                    },
                    "required": ["reason", "text"],
                },
            },
        },
        "required": ["version", "status", "allow"],
    }


def build_policy_tool_schema() -> dict[str, Any]:
    """Return the tool registration shape for ``build_policy``."""
    return {
        "type": "function",
        "function": {
            "name": BUILD_POLICY_TOOL_NAME,
            "description": (
                "Validate a complete draft candidate against the closed schema, "
                "preview it against the same local pending cohort as the current "
                "session draft, and return aggregate preview metrics. The host "
                "supplies current_draft from session state. Does not approve "
                "refunds or activate policy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_policy": _candidate_policy_schema(),
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Confirmed or labeled assumptions.",
                    },
                    "cannot_express": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Unsupported requirements that cannot be expressed "
                            "in the closed schema."
                        ),
                    },
                },
                "required": ["candidate_policy"],
            },
        },
    }


def query_history_tool_schema() -> dict[str, Any]:
    """Return the tool registration shape for local ``query_history``."""
    return {
        "type": "function",
        "function": {
            "name": QUERY_HISTORY_TOOL_NAME,
            "description": (
                "Query the local synthetic history.csv sample with optional "
                "filters. Returns match counts, simple breakdowns, and a small "
                "sample of rows to help ask clarifying or edge-case questions. "
                "Historical decisions are descriptive only, not ground-truth "
                "labels. Does not approve refunds or activate policy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stated_reason": {
                        "type": "string",
                        "description": (
                            "Dictionary stated_reason. Spacing and case are "
                            "ignored, and the sample spellings damaged and late "
                            "are included."
                        ),
                    },
                    "amount_usd_lte": {
                        "type": "number",
                        "description": "Keep rows with amount_usd <= this value.",
                    },
                    "amount_usd_gt": {
                        "type": "number",
                        "description": "Keep rows with amount_usd > this value.",
                    },
                    "tenure_months_gte": {
                        "type": "integer",
                        "description": "Keep rows with tenure_months >= this value.",
                    },
                    "days_since_delivery_lte": {
                        "type": "integer",
                        "description": (
                            "Keep rows with days_since_delivery <= this value."
                        ),
                    },
                    "days_since_delivery_gte": {
                        "type": "integer",
                        "description": (
                            "Keep rows with days_since_delivery >= this value."
                        ),
                    },
                    "fraud_flag": {
                        "type": "string",
                        "description": (
                            "Fraud check. 'no' matches the clear tokens "
                            "no, n, and false. 'yes' matches raised tokens."
                        ),
                    },
                    "sample_limit": {
                        "type": "integer",
                        "description": "Max sample rows to return (1-20, default 5).",
                    },
                },
            },
        },
    }


def registered_tool_schemas() -> list[dict[str, Any]]:
    """Return all tool schemas registered for the manager agent."""
    return [build_policy_tool_schema(), query_history_tool_schema()]


def registered_tool_names() -> tuple[str, ...]:
    """Return tool names registered for the manager agent."""
    return (BUILD_POLICY_TOOL_NAME, QUERY_HISTORY_TOOL_NAME)


_THRESHOLD_KINDS = {
    "amount_usd_lte": "amount_usd_lte",
    "amount_usd_gt": "amount_usd_lte",
    "tenure_months_gte": "tenure_months_gte",
    "days_since_delivery_lte": "days_since_delivery_lte",
    "days_since_delivery_gte": "days_since_delivery_lte",
}
def _number_token(value: Any) -> str | None:
    """Render a threshold the way a manager would have typed it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value)


def _number_stated(user_text: str, value: Any) -> bool:
    """Return whether the manager's own messages contain this threshold."""
    token = _number_token(value)
    if token is None:
        return False
    return re.search(rf"(?<!\d){re.escape(token)}(?!\d)", user_text) is not None


def policy_confirmation_blockers(
    draft: Mapping[str, Any], user_messages: Sequence[Mapping[str, str]]
) -> list[dict[str, str]]:
    """Return reasons a built draft must not be offered for approval yet.

    The page uses this instead of trusting the model. A threshold in the draft
    counts only if the manager stated or picked that number. A vague phrase
    such as "good customers" or "small stuff" does not by itself require a
    tenure or an amount.
    """
    user_text = "\n".join(
        message.get("content") or ""
        for message in user_messages
        if message.get("role") == "user"
    )
    blockers: list[dict[str, str]] = []
    seen: set[str] = set()
    for rule in draft.get("allow") or []:
        if not isinstance(rule, Mapping):
            continue
        for group in rule.get("all_of") or []:
            conditions = group.get("when") if isinstance(group, Mapping) else None
            if not isinstance(conditions, Mapping):
                continue
            for field, kind in _THRESHOLD_KINDS.items():
                if field not in conditions or kind in seen:
                    continue
                if _number_stated(user_text, conditions[field]):
                    continue
                seen.add(kind)
                blockers.append(
                    {"kind": kind, "detail": _unconfirmed_detail(kind, conditions[field])}
                )
    return blockers


def _unconfirmed_detail(kind: str, value: Any) -> str:
    shown = _number_token(value) or value
    if kind == "amount_usd_lte":
        return f"This draft uses a ${shown} amount you have not chosen."
    if kind == "tenure_months_gte":
        return f"This draft uses {shown} months of tenure, which you have not chosen."
    return f"This draft uses a {shown}-day window you have not chosen."


def suggest_policy_name(
    draft: Mapping[str, Any], policies_dir: Path = POLICIES_DIR
) -> str:
    """Build a free, editable file name from the draft's rule ids."""
    parts: list[str] = []
    for rule in draft.get("allow") or []:
        if not isinstance(rule, Mapping) or not isinstance(rule.get("id"), str):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", rule["id"].strip().lower()).strip("-")
        if slug:
            parts.append(slug)
    base = "-".join(parts)[:50].strip("-") or "refund-policy"
    if not _POLICY_NAME_PATTERN.fullmatch(base):
        base = "refund-policy"
    name = base
    for suffix in range(2, 100):
        if policy_name_error(name, policies_dir) is None:
            return name
        extra = f"-{suffix}"
        name = base[: 50 - len(extra)].strip("-") + extra
    return name


# Only definitions of the clarify marker, preset values, and preset-mention regexes.
CLARIFY_PRESET_VALUES: dict[str, tuple[int, ...]] = {
    "amount_usd_lte": (25, 30, 50),
    "tenure_months_gte": (6, 12, 24),
    "days_since_delivery_lte": (7, 14, 30),
}
CLARIFY_MARKER_PATTERN = re.compile(
    r"\[\[clarify:(" + "|".join(CLARIFY_PRESET_VALUES) + r")"
    r"(?:\|([^|\]]{1,80}))?"
    r"(?:\|([0-9]{1,6}(?:\s*,\s*[0-9]{1,6}){1,5}))?"
    r"\]\]"
)


def _preset_mention_pattern(values: tuple[int, ...]) -> re.Pattern[str]:
    """Match the preset numbers in order, with a little text between them."""
    body = r"\D{1,12}".join(str(value) for value in values)
    return re.compile(rf"\b{body}\b")


# The preset lists the agent offers; spotted when it forgets the marker.
CLARIFY_PRESET_PATTERNS = {
    kind: _preset_mention_pattern(values) for kind, values in CLARIFY_PRESET_VALUES.items()
}
_CLARIFY_BULLET = re.compile(r"^(?:\d+[\).\]]\s*|[-*]\s*)")
_CLARIFY_FALLBACK = {
    "amount_usd_lte": "The refund amount in the question above",
    "tenure_months_gte": "How long the customer has been with you",
    "days_since_delivery_lte": "How many days since delivery",
}


def parse_clarify_marker(text: str) -> dict[str, Any] | None:
    """Read the topic and choices the agent put in one clarify marker.

    A marker is ``[[clarify:amount_usd_lte|Small stuff from good customers|25,30,50]]``.
    The kind is which threshold is being asked. The topic is the short phrase
    the agent wrote. The values are the button amounts, in the agent's order.
    A bare ``[[clarify:amount_usd_lte]]`` still returns the kind, with no topic
    and no values. Two to four positive whole numbers are accepted.
    """
    if not isinstance(text, str):
        return None
    match = CLARIFY_MARKER_PATTERN.search(text)
    if match is None:
        return None
    topic = (match.group(2) or "").strip()
    if not (2 <= len(topic) <= 80) or any(character in topic for character in "[]{}"):
        topic = ""
    return {
        "kind": match.group(1),
        "topic": topic or None,
        "values": _clarify_marker_values(match.group(3)),
    }


def _clarify_marker_values(raw: str | None) -> tuple[int, ...] | None:
    """Return two to four distinct positive choices, or None when unusable."""
    if not raw:
        return None
    numbers: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            return None
        number = int(part)
        if number <= 0 or number > 100000 or number in numbers:
            return None
        numbers.append(number)
    if not 2 <= len(numbers) <= 4:
        return None
    return tuple(numbers)


def clarify_pick_caption(kind: str, assistant_text: str) -> str:
    """Caption for the on-screen pick list, naming what the question is about.

    The buttons are a dollar amount, a tenure, or a day count. The caption
    uses the topic in the assistant's question, such as "Small stuff from
    good customers", instead of a generic field name.
    """
    subject = _clarify_subject(kind, assistant_text)
    topic = subject or _CLARIFY_FALLBACK[kind]
    return (
        f"{topic}. Choose a preset or enter a custom value — "
        "one clear answer so we do not invent a threshold."
    )


def _clarify_subject(kind: str, assistant_text: str) -> str | None:
    """Return the short topic on the paragraph that offers this pick list."""
    if kind not in CLARIFY_PRESET_PATTERNS or not isinstance(assistant_text, str):
        return None
    cleaned = CLARIFY_MARKER_PATTERN.sub("", assistant_text)
    pattern = CLARIFY_PRESET_PATTERNS[kind]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    for paragraph in paragraphs:
        if not pattern.search(paragraph) or ":" not in paragraph:
            continue
        head = paragraph.split(":", 1)[0].strip().splitlines()[-1]
        head = _CLARIFY_BULLET.sub("", head).strip().strip("\"'")
        if 2 <= len(head) <= 80 and any(character.isalpha() for character in head):
            return head
    return None
# Words that show the manager raised the topic themselves.
_TOPIC_MENTION_PATTERNS = {
    "amount_usd_lte": re.compile(
        r"\$|\d\s*(?:usd|dollars?)\b|\b(?:cheap|low[- ]value|amount|price|cost|"
        r"cap|limit|under|below|up to|less than|expensive)\b",
        re.IGNORECASE,
    ),
    "tenure_months_gte": re.compile(
        r"\btenure\b|\bhow long\b|\b(?:been|for)\s+(?:a\s+)?customers?\b|"
        r"\b\d+\s*(?:months?|years?)\b",
        re.IGNORECASE,
    ),
    "days_since_delivery_lte": re.compile(
        r"\b(?:days?|weeks?|window|within|recent|since delivery|delivered)\b",
        re.IGNORECASE,
    ),
}
_TOPIC_LABELS = {
    "amount_usd_lte": "an amount limit",
    "tenure_months_gte": "customer tenure",
    "days_since_delivery_lte": "a delivery time window",
}


def unrequested_question_topics(
    reply_text: str, manager_texts: Sequence[str]
) -> list[str]:
    """Return labels of topics the reply asks about that the manager never raised.

    Used by the agent graph's guard step, so the model cannot add conditions
    (an amount cap, tenure, a window) the manager did not mention.
    """
    asked = {match.group(1) for match in CLARIFY_MARKER_PATTERN.finditer(reply_text)}
    asked.update(
        kind for kind, pattern in CLARIFY_PRESET_PATTERNS.items() if pattern.search(reply_text)
    )
    manager_text = "\n".join(manager_texts)
    return [
        _TOPIC_LABELS[kind]
        for kind in sorted(asked)
        if not _TOPIC_MENTION_PATTERNS[kind].search(manager_text)
    ]


def policy_name_error(name: str, policies_dir: Path = POLICIES_DIR) -> str | None:
    """Return why ``name`` cannot be used for a new saved policy, or ``None``.

    Names cannot contain a dot, so they never collide with the template or
    example files in ``policies/``.
    """
    if not _POLICY_NAME_PATTERN.fullmatch(name or ""):
        return (
            "Use 1-50 characters: lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit."
        )
    if (policies_dir / f"{name}.json").exists():
        return f"A policy named “{name}” already exists. Pick another name."
    return None


def save_policy_draft(
    name: str, draft: Mapping[str, Any], policies_dir: Path = POLICIES_DIR
) -> Path:
    """Write a validated draft to ``policies/<name>.json``; never overwrite.

    Saving is not activation: the file keeps ``status: "draft"`` and nothing
    points live intake at it.
    """
    name_error = policy_name_error(name, policies_dir)
    if name_error:
        raise ValueError(name_error)
    if draft.get("status") != "draft":
        raise ValueError("Only drafts can be saved.")
    schema_errors = policy_schema_errors(draft)
    if schema_errors:
        raise ValueError(" ".join(schema_errors))

    policies_dir.mkdir(parents=True, exist_ok=True)
    path = policies_dir / f"{name}.json"
    # Mode "x" fails if the file appeared since the name check.
    # _json_dumps keeps Decimal operands as JSON numbers so a reload still validates.
    with path.open("x", encoding="utf-8") as handle:
        handle.write(_json_dumps(draft))
        handle.write("\n")
    return path


def apply_build_policy_result(
    current_draft: Mapping[str, Any],
    tool_result: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return the next session draft and whether it changed.

    The session draft updates only when ``tool_result["status"] == "proposed"``.
    """
    if tool_result.get("status") == "proposed":
        draft = tool_result.get("draft")
        if isinstance(draft, dict):
            return deepcopy(draft), True
    return deepcopy(dict(current_draft)), False


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


def _parse_tool_arguments(
    arguments: Mapping[str, Any] | str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(parsed, None)`` or ``(None, error_payload)``."""
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None, {
                "status": "invalid",
                "errors": ["Tool arguments must be valid JSON."],
            }
    else:
        parsed = dict(arguments)
    if not isinstance(parsed, dict):
        return None, {
            "status": "invalid",
            "errors": ["Tool arguments must be a JSON object."],
        }
    return parsed, None


def execute_build_policy_arguments(
    arguments: Mapping[str, Any] | str,
    current_draft: Mapping[str, Any],
) -> dict[str, Any]:
    """Invoke local ``build_policy``, always injecting the session current draft."""
    parsed, error = _parse_tool_arguments(arguments)
    if error is not None:
        return error

    return build_policy(
        parsed.get("candidate_policy"),
        deepcopy(dict(current_draft)),
        assumptions=_as_str_tuple(parsed.get("assumptions")),
        cannot_express=_as_str_tuple(parsed.get("cannot_express")),
    )


def _row_matches_history_filters(row: Mapping[str, str], filters: Mapping[str, Any]) -> bool:
    """Return whether a history row matches optional query filters."""
    # Descriptive field filter, not evaluate() approve_full; history_evidence owns that count (rails stay out).
    stated_reason = filters.get("stated_reason")
    if stated_reason is not None and compare_policy_field("stated_reason", stated_reason, row) is not True:
        return False

    fraud_flag = filters.get("fraud_flag")
    if fraud_flag is not None and compare_policy_field("fraud_flag", fraud_flag, row) is not True:
        return False

    amount = parse_amount(row.get("amount_usd"))
    if filters.get("amount_usd_lte") is not None:
        if amount is None or amount > Decimal(str(filters["amount_usd_lte"])):
            return False
    if filters.get("amount_usd_gt") is not None:
        if amount is None or amount <= Decimal(str(filters["amount_usd_gt"])):
            return False

    if filters.get("tenure_months_gte") is not None:
        if compare_policy_field("tenure_months_gte", filters["tenure_months_gte"], row) is not True:
            return False

    if filters.get("days_since_delivery_lte") is not None:
        if compare_policy_field("days_since_delivery_lte", filters["days_since_delivery_lte"], row) is not True:
            return False

    if filters.get("days_since_delivery_gte") is not None:
        if compare_policy_field("days_since_delivery_gte", filters["days_since_delivery_gte"], row) is not True:
            return False

    return True


def query_history(filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Filter local history.csv and return counts, breakdowns, and a small sample.

    Historical ``decision`` / ``decided_by`` values are descriptive sample
    evidence only — not labels that prove a rule is correct or safe.
    """
    filters = dict(filters or {})
    try:
        sample_limit = int(filters.pop("sample_limit", 5) or 5)
    except (TypeError, ValueError):
        return {"status": "invalid", "errors": ["sample_limit must be an integer."]}
    sample_limit = max(1, min(sample_limit, 20))

    try:
        rows = load_rows(HISTORY_PATH)
    except OSError:
        return {"status": "error", "errors": ["Could not read history.csv."]}

    matched = [row for row in rows if _row_matches_history_filters(row, filters)]
    total = Decimal("0.00")
    reason_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    for row in matched:
        amount = parse_amount(row.get("amount_usd"))
        if amount is not None:
            total += amount
        reason_counts[(row.get("stated_reason") or "").strip() or "(blank)"] += 1
        decision_counts[(row.get("decision") or "").strip() or "(blank)"] += 1

    sample = [
        {field: row.get(field, "") for field in _HISTORY_SAMPLE_FIELDS}
        for row in matched[:sample_limit]
    ]
    return {
        "status": "ok",
        "disclaimer": (
            "Synthetic assignment sample. Historical decisions are not "
            "ground-truth labels and do not authorize policy activation."
        ),
        "filters_applied": filters,
        "match_count": len(matched),
        "cohort_size": len(rows),
        "matched_amount_total_usd": format(total.quantize(_CENT), "f"),
        "by_stated_reason": dict(reason_counts),
        "by_decision": dict(decision_counts),
        "sample_rows": sample,
    }


def execute_query_history_arguments(
    arguments: Mapping[str, Any] | str,
) -> dict[str, Any]:
    """Parse tool arguments and run ``query_history``."""
    parsed, error = _parse_tool_arguments(arguments)
    if error is not None:
        return error
    return query_history(parsed)


def execute_registered_tool(
    name: str,
    arguments: Mapping[str, Any] | str,
    current_draft: Mapping[str, Any],
) -> dict[str, Any]:
    """Dispatch a registered tool name to the local host implementation."""
    if name == BUILD_POLICY_TOOL_NAME:
        return execute_build_policy_arguments(arguments, current_draft)
    if name == QUERY_HISTORY_TOOL_NAME:
        return execute_query_history_arguments(arguments)
    return {
        "status": "invalid",
        "errors": [f"Unsupported tool '{name}'."],
    }


def format_preview_summary_for_manager(
    preview_summary: Mapping[str, Any] | None,
) -> str:
    """Format aggregate-only preview metrics for the manager UI."""
    if not preview_summary:
        return ""
    prior = preview_summary.get("prior") or {}
    candidate = preview_summary.get("candidate") or {}
    delta = preview_summary.get("delta") or {}
    return (
        "Pending requests this policy would match:\n"
        f"- Prior draft: {prior.get('approved_count', 0)} matches, "
        f"${prior.get('approved_total_usd', '0.00')}\n"
        f"- Candidate: {candidate.get('approved_count', 0)} matches, "
        f"${candidate.get('approved_total_usd', '0.00')}\n"
        f"- Delta: {delta.get('approved_count', 0)} matches, "
        f"${delta.get('approved_total_usd', '0.00')}"
    )


def tool_result_for_model(result: Mapping[str, Any]) -> dict[str, Any]:
    """Filter tool payloads before they are sent back to the model."""
    if result.get("status") == "ok" and "sample_rows" in result:
        # query_history payload — assignment demo may include sample history rows.
        allowed = (
            "status",
            "disclaimer",
            "filters_applied",
            "match_count",
            "cohort_size",
            "matched_amount_total_usd",
            "by_stated_reason",
            "by_decision",
            "sample_rows",
            "errors",
        )
        return {key: result[key] for key in allowed if key in result}
    allowed = (
        "status",
        "draft",
        "assumptions",
        "cannot_express",
        "preview_summary",
        "history_evidence",
        "errors",
    )
    return {key: result[key] for key in allowed if key in result}


def assistant_text_from_tool_results(
    tool_results: Sequence[Mapping[str, Any]],
    preview_summary: Mapping[str, Any] | None,
) -> str:
    """Build a fallback assistant reply from the last local tool result."""
    if not tool_results:
        return ""
    last = tool_results[-1]
    status = last.get("status")
    if status == "proposed":
        text = (
            "Candidate policy proposed. Review the aggregate preview below. "
            "This is not an activation or a refund."
        )
        if preview_summary:
            text += "\n\n" + format_preview_summary_for_manager(preview_summary)
        return text
    if status == "cannot_express":
        items = last.get("cannot_express") or []
        return (
            "Part of the request cannot be expressed in the closed schema:\n"
            + "\n".join(f"- {item}" for item in items)
        )
    if status in {"invalid", "error"}:
        errors = last.get("errors") or ["Unknown validation or preview error."]
        return "Could not propose a draft:\n" + "\n".join(
            f"- {item}" for item in errors
        )
    return ""


def conversation_messages_for_provider(
    history: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    """Copy user/assistant chat turns for the provider (no tool/system rows)."""
    return [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ]


def provider_payload_contains_forbidden_customer_data(
    payload: Any,
    *,
    forbidden_substrings: Sequence[str] = (),
) -> bool:
    """Return True if any forbidden customer-request marker appears in ``payload``."""
    blob = json.dumps(payload, default=str)
    return any(marker and marker in blob for marker in forbidden_substrings)


def assert_messages_safe_for_provider(
    messages: Sequence[Mapping[str, Any]],
    *,
    forbidden_substrings: Sequence[str] = (),
) -> None:
    """Raise ``ValueError`` if provider messages include forbidden customer data."""
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("Provider message must be a mapping.")
        if provider_payload_contains_forbidden_customer_data(
            message, forbidden_substrings=forbidden_substrings
        ):
            raise ValueError(
                "Provider payload must not include customer request rows, IDs, "
                "or customer/support note text."
            )


def ensure_session_defaults(
    session_state: MutableMapping[str, Any],
    *,
    messages_key: str = "policy_management_messages",
    draft_key: str = "policy_management_draft",
    preview_key: str = "policy_management_preview_summary",
) -> None:
    """Initialize Streamlit session keys for the policy-management page."""
    if messages_key not in session_state:
        session_state[messages_key] = []
    if draft_key not in session_state:
        session_state[draft_key] = load_template_draft()
    if preview_key not in session_state:
        session_state[preview_key] = None

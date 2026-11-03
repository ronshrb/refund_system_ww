"""Helpers for the manager policy-drafting agent host.

Does not approve refunds or activate policy. History query results are for the
assignment demo (synthetic sample data); treat historical decisions as
descriptive only, not ground-truth labels.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from dotenv import load_dotenv

from utils.data.data_processing import parse_amount
from utils.data.dataloader import load_rows
from utils.policy.policy_draft_tool import build_policy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "policy" / "policy.template.json"
HISTORY_PATH = PROJECT_ROOT / "data" / "raw" / "history.csv"

load_dotenv(PROJECT_ROOT / ".env", override=False)
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()

OFFLINE_MESSAGE = (
    "OpenAI is not configured. Set `OPENAI_API_KEY` in the repository-root "
    "`.env` file to enable the policy drafting agent. This offline path does "
    "not invent a model reply and cannot call `build_policy`."
)

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
    """Load a fresh draft from ``policy/policy.template.json``."""
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def build_policy_tool_schema() -> dict[str, Any]:
    """Return the OpenAI tools-API registration shape for ``build_policy`` only."""
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
                    "candidate_policy": {
                        "type": "object",
                        "description": (
                            "Complete candidate policy object with version, "
                            "status 'draft', comment, and allow rules."
                        ),
                    },
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
                "additionalProperties": False,
            },
        },
    }


def query_history_tool_schema() -> dict[str, Any]:
    """Return the OpenAI tools-API shape for local ``query_history``."""
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
                        "description": "Exact stated_reason to match, if any.",
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
                        "description": "Exact fraud_flag to match (e.g. 'no').",
                    },
                    "sample_limit": {
                        "type": "integer",
                        "description": "Max sample rows to return (1-20, default 5).",
                    },
                },
                "additionalProperties": False,
            },
        },
    }


def registered_tool_schemas() -> list[dict[str, Any]]:
    """Return all OpenAI tool schemas registered for the manager agent."""
    return [build_policy_tool_schema(), query_history_tool_schema()]


def registered_tool_names() -> tuple[str, ...]:
    """Return tool names registered for the manager agent."""
    return (BUILD_POLICY_TOOL_NAME, QUERY_HISTORY_TOOL_NAME)


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


def _parse_int_field(row: Mapping[str, str], field: str) -> int | None:
    raw = (row.get(field) or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _row_matches_history_filters(row: Mapping[str, str], filters: Mapping[str, Any]) -> bool:
    """Return whether a history row matches optional query filters."""
    stated_reason = filters.get("stated_reason")
    if stated_reason is not None and (row.get("stated_reason") or "") != str(stated_reason):
        return False

    fraud_flag = filters.get("fraud_flag")
    if fraud_flag is not None and (row.get("fraud_flag") or "") != str(fraud_flag):
        return False

    amount = parse_amount(row.get("amount_usd"))
    if filters.get("amount_usd_lte") is not None:
        if amount is None or amount > Decimal(str(filters["amount_usd_lte"])):
            return False
    if filters.get("amount_usd_gt") is not None:
        if amount is None or amount <= Decimal(str(filters["amount_usd_gt"])):
            return False

    if filters.get("tenure_months_gte") is not None:
        value = _parse_int_field(row, "tenure_months")
        try:
            bound = int(filters["tenure_months_gte"])
        except (TypeError, ValueError):
            return False
        if value is None or value < bound:
            return False

    if filters.get("days_since_delivery_lte") is not None:
        value = _parse_int_field(row, "days_since_delivery")
        try:
            bound = int(filters["days_since_delivery_lte"])
        except (TypeError, ValueError):
            return False
        if value is None or value > bound:
            return False

    if filters.get("days_since_delivery_gte") is not None:
        value = _parse_int_field(row, "days_since_delivery")
        try:
            bound = int(filters["days_since_delivery_gte"])
        except (TypeError, ValueError):
            return False
        if value is None or value < bound:
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
        "Local pending-cohort preview (aggregates only; not a refund or activation):\n"
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
        "errors",
    )
    return {key: result[key] for key in allowed if key in result}


def collect_streamed_message(
    stream: Any, on_text_delta: Callable[[str], None]
) -> SimpleNamespace:
    """Consume a streamed chat completion into a non-streamed-shaped message.

    Text deltas are forwarded to ``on_text_delta`` as they arrive. Tool-call
    fragments arrive split across chunks and are joined by their ``index``.
    The return value has ``.content`` and ``.tool_calls`` (each with ``.id``,
    ``.function.name``, ``.function.arguments``) like a non-streamed message.
    """
    content_parts: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            content_parts.append(delta.content)
            on_text_delta(delta.content)
        for fragment in delta.tool_calls or []:
            call = calls.setdefault(
                fragment.index, {"id": "", "name": "", "arguments": ""}
            )
            if fragment.id:
                call["id"] = fragment.id
            if fragment.function and fragment.function.name:
                call["name"] += fragment.function.name
            if fragment.function and fragment.function.arguments:
                call["arguments"] += fragment.function.arguments

    tool_calls = [
        SimpleNamespace(
            id=call["id"],
            function=SimpleNamespace(name=call["name"], arguments=call["arguments"]),
        )
        for _, call in sorted(calls.items())
    ]
    return SimpleNamespace(content="".join(content_parts), tool_calls=tool_calls)


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

"""Helpers for the manager policy-drafting agent host.

No refund approvals, policy activation, or customer request rows/notes are
sent to a provider from these helpers.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from dotenv import load_dotenv

from utils.policy.policy_draft_tool import build_policy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "policy" / "policy.template.json"

load_dotenv(PROJECT_ROOT / ".env", override=False)
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()

OFFLINE_MESSAGE = (
    "OpenAI is not configured. Set `OPENAI_API_KEY` in the repository-root "
    "`.env` file to enable the policy drafting agent. This offline path does "
    "not invent a model reply and cannot call `build_policy`."
)

BUILD_POLICY_TOOL_NAME = "build_policy"


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


def registered_tool_names() -> tuple[str, ...]:
    """Return the only tool name registered for the manager agent."""
    return (BUILD_POLICY_TOOL_NAME,)


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


def execute_build_policy_arguments(
    arguments: Mapping[str, Any] | str,
    current_draft: Mapping[str, Any],
) -> dict[str, Any]:
    """Invoke local ``build_policy``, always injecting the session current draft."""
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {
                "status": "invalid",
                "errors": ["Tool arguments must be valid JSON."],
            }
    else:
        parsed = dict(arguments)

    if not isinstance(parsed, dict):
        return {
            "status": "invalid",
            "errors": ["Tool arguments must be a JSON object."],
        }

    return build_policy(
        parsed.get("candidate_policy"),
        deepcopy(dict(current_draft)),
        assumptions=_as_str_tuple(parsed.get("assumptions")),
        cannot_express=_as_str_tuple(parsed.get("cannot_express")),
    )


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
    """Keep only status, draft, assumptions, aggregates, and errors for the model."""
    allowed = (
        "status",
        "draft",
        "assumptions",
        "cannot_express",
        "preview_summary",
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

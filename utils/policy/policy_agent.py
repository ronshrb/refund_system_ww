"""Manager policy-drafting agent: one OpenAI turn with local ``build_policy``.

Never approves refunds, activates policy, or sends customer request rows/notes
to a provider.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from utils.policy import policy_agent_utils as utils

# Re-export helpers the Streamlit page and tests import from this module.
OPENAI_API_KEY = utils.OPENAI_API_KEY
OFFLINE_MESSAGE = utils.OFFLINE_MESSAGE
BUILD_POLICY_TOOL_NAME = utils.BUILD_POLICY_TOOL_NAME
load_template_draft = utils.load_template_draft
build_policy_tool_schema = utils.build_policy_tool_schema
registered_tool_names = utils.registered_tool_names
apply_build_policy_result = utils.apply_build_policy_result
execute_build_policy_arguments = utils.execute_build_policy_arguments
format_preview_summary_for_manager = utils.format_preview_summary_for_manager
conversation_messages_for_provider = utils.conversation_messages_for_provider
provider_payload_contains_forbidden_customer_data = (
    utils.provider_payload_contains_forbidden_customer_data
)
assert_messages_safe_for_provider = utils.assert_messages_safe_for_provider
ensure_session_defaults = utils.ensure_session_defaults

SYSTEM_INSTRUCTIONS = """You are the Policy Drafter for a Refund Operations Manager.

You help turn manager instructions into complete draft policy candidates. You do
not approve or reject refund requests, call payments, activate or version
policy, write a live policy file, or change a live-policy pointer. A preview is
not a refund decision.

Closed policy shape: {version, status, comment, allow}. A rule has exactly
{id, action, all_of} with action "approve_full"; each all_of group has a
nonempty when object. Supported when conditions only:
- stated_reason (wrong_size | changed_mind | arrived_damaged | wrong_item |
  never_arrived | late_delivery)
- days_since_delivery_gte, days_since_delivery_lte (integers)
- amount_usd_gt, amount_usd_lte (numeric)
- tenure_months_gte (integer)
- fraud_flag: "no"
- customer_id_present: true
- customer_note_blank: true
- support_note_blank: true
- request_id_not_already_paid: true
Do not invent fields, operators, rails, reject actions, or partial refunds.

Clarification before tools:
- Ask focused clarifying questions before any tool call when intent, fields,
  operators, or numeric thresholds are ambiguous.
- For vague phrases such as "good customers" or "small stuff", ask what the
  manager means and guide them toward supported fields and concrete values
  (for example tenure_months_gte or an amount_usd range). Do not invent an
  unconfirmed threshold.
- Never silently invent numeric values. For every numeric value not explicitly
  stated by the manager, state the exact value and rationale, label it
  unverified, and ask the manager to confirm or correct it before submitting a
  candidate that uses it.

Function tool:
- After policy-affecting answers are clear, call only `build_policy`.
- Pass a complete candidate_policy based on the current session draft supplied
  in this prompt. Optionally pass assumptions and cannot_express string lists.
- The host injects current_draft from session state; do not invent a different
  current draft.
- For a clearly stated but unsupported requirement, put a concise explanation
  in cannot_express instead of approximating or omitting it.
- Do not call the tool while required answers remain unclear.

Data boundary:
- Never ask for or send history/pending request rows, request IDs, customer
  fields, or the free-text contents of customer_note / support_note.
- Continue with manager text, the schema, and the current draft only.

After a tool result:
- status "proposed" means the local host may adopt the returned draft; summarize
  aggregate preview counts/totals/deltas only.
- status "invalid", "error", or "cannot_express" leaves the session draft
  unchanged; explain clearly.
- Never relay individual preview rows or treat a proposal as activation.
"""


def clarification_guidance_present(system_text: str) -> bool:
    """Return whether system text encodes clarification-before-tool rules."""
    collapsed = " ".join(system_text.split())
    required = (
        "Ask focused clarifying questions before any tool call",
        "good customers",
        "small stuff",
        "Do not invent an unconfirmed threshold",
        "Never silently invent numeric values",
    )
    return all(fragment in collapsed for fragment in required)


def build_system_message(current_draft: Mapping[str, Any]) -> str:
    """Build the system message from instructions and the current session draft."""
    return "\n\n".join(
        [
            SYSTEM_INSTRUCTIONS,
            "Current session draft (JSON):",
            json.dumps(current_draft, indent=2, default=str),
        ]
    )


def run_policy_agent_turn(
    *,
    user_text: str,
    history: Sequence[Mapping[str, str]],
    current_draft: Mapping[str, Any],
    client: Any | None = None,
    model: str = "gpt-4o-mini",
    max_tool_rounds: int = 4,
) -> dict[str, Any]:
    """Run one manager turn with optional OpenAI tool calling.

    When no API key is configured, returns an honest offline message without
    pretending to be the model. Session draft updates only on ``proposed``.
    """
    draft = deepcopy(dict(current_draft))
    if not OPENAI_API_KEY:
        return {
            "assistant_text": OFFLINE_MESSAGE,
            "draft": draft,
            "draft_updated": False,
            "preview_summary": None,
            "tool_results": [],
            "offline": True,
            "provider_messages": None,
        }

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_message(draft)},
        *conversation_messages_for_provider(history),
        {"role": "user", "content": user_text},
    ]
    tools = [build_policy_tool_schema()]

    tool_results: list[dict[str, Any]] = []
    preview_summary: dict[str, Any] | None = None
    draft_updated = False
    assistant_text = ""

    for _ in range(max_tool_rounds):
        choice = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        ).choices[0].message

        tool_calls = list(choice.tool_calls or [])
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": choice.content or "",
        }
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]
        messages.append(assistant_message)

        if not tool_calls:
            assistant_text = (choice.content or "").strip()
            break

        for call in tool_calls:
            if call.function.name != BUILD_POLICY_TOOL_NAME:
                result = {
                    "status": "invalid",
                    "errors": [f"Unsupported tool '{call.function.name}'."],
                }
            else:
                result = execute_build_policy_arguments(
                    call.function.arguments, draft
                )
            tool_results.append(result)
            next_draft, changed = apply_build_policy_result(draft, result)
            if changed:
                draft = next_draft
                draft_updated = True
                preview_summary = result.get("preview_summary")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(
                        utils.tool_result_for_model(result), default=str
                    ),
                }
            )
    else:
        assistant_text = (
            "Stopped after the maximum number of tool rounds without a final "
            "assistant reply. The session draft was updated only if a proposed "
            "result was returned."
        )

    if not assistant_text:
        assistant_text = utils.assistant_text_from_tool_results(
            tool_results, preview_summary
        )

    return {
        "assistant_text": assistant_text or "(No assistant text returned.)",
        "draft": draft,
        "draft_updated": draft_updated,
        "preview_summary": preview_summary,
        "tool_results": tool_results,
        "offline": False,
        "provider_messages": messages,
    }

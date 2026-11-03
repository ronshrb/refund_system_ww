"""Manager policy-drafting agent: one OpenAI turn with local tools.

Never approves refunds or activates policy. History query is for the assignment
demo on synthetic sample data; historical decisions are not ground-truth labels.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable, Mapping, Sequence

from utils.policy.policy_agent_utils import (
    BUILD_POLICY_TOOL_NAME,
    OFFLINE_MESSAGE,
    OPENAI_API_KEY,
    QUERY_HISTORY_TOOL_NAME,
    apply_build_policy_result,
    assert_messages_safe_for_provider,
    assistant_text_from_tool_results,
    build_policy_tool_schema,
    collect_streamed_message,
    conversation_messages_for_provider,
    ensure_session_defaults,
    execute_build_policy_arguments,
    execute_registered_tool,
    format_preview_summary_for_manager,
    load_template_draft,
    provider_payload_contains_forbidden_customer_data,
    query_history_tool_schema,
    registered_tool_names,
    registered_tool_schemas,
    tool_result_for_model,
)

DATA_DICTIONARY_TEXT = """
request_id: Unique ID per refund request. RF-1xxx = web form, RF-2xxx = support desk.
requested_at: Date the customer submitted the request (YYYY-MM-DD).
customer_id: Customer account ID.
tenure_months: Months since the customer's account was created.
prior_refunds_12m: Number of refunds granted to this customer in the 12 months before this request.
amount_usd: Requested refund amount in US dollars (number).
category: Product category: apparel, home, electronics.
payment_method: card or paypal.
stated_reason: Reason the customer selected: wrong_size, changed_mind, arrived_damaged, wrong_item, never_arrived, late_delivery.
customer_note: Free text written by the customer. Optional.
support_note: Free text written by the support agent who handled the ticket. Optional.
days_since_delivery: Days between delivery (per carrier) and the request.
fraud_flag: yes / no. Set by the payment provider's risk check.
decision: approved, rejected, partial, or pending. (Post-decision; not an intake policy condition.)
decided_by: 'auto' for the existing auto-approve rule, otherwise the reviewer's name. (Post-decision.)
outcome_note: Anything that happened after the decision. (Post-decision.)
decided_at: When the decision was recorded. (Post-decision.)
review_minutes: Minutes the reviewer spent (blank for auto). (Post-decision.)
Note: Column descriptions describe intended contents; the sample data does not always comply.
""".strip()

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

How to write rules (the validator rejects anything else):
- Keep "version" from the current draft and set "status" to exactly "draft".
- Conditions inside one "when" are AND. Separate rules are OR.
- stated_reason takes exactly one string, never a list. For several reasons
  (for example our mistakes), write one rule per reason.
- Each rule id is unique, short, and snake_case.
- Example: {"id": "changed_mind_14d", "action": "approve_full", "all_of":
  [{"when": {"stated_reason": "changed_mind", "days_since_delivery_lte": 14}}]}
- Manager words to reasons: wrong item or picking error -> wrong_item;
  damaged -> arrived_damaged; lost or not delivered -> never_arrived. If a
  phrase has no matching reason, say so instead of picking a close one.

How you talk to the manager:
- The manager is not technical. Use plain business language only.
- Never show policy JSON, rule ids, field names (such as amount_usd_lte or
  stated_reason), operators, "all_of", or a "policy structure" breakdown. The
  page already shows the draft separately.
- Do not list the safety checks (fraud, notes, customer id, already paid) as
  rule conditions; just say standard safety checks still apply.
- Do not announce that you are about to build the draft. If everything is
  clear, call build_policy in the same reply, then summarize the result.

Light chain of thought (keep short, plain language):
- Before asking a question or calling a tool, open with at most 3 short
  bullets: what you understood, what is still unclear, and one edge case
  worth checking. No technical terms.

Clarification before build_policy:
- Ask focused clarifying questions when intent, fields, operators, or numeric
  thresholds are ambiguous.
- Ask in business terms. The manager may not know schema field names; do not
  ask them to pick columns or operators. Propose a plain-language mapping to a
  supported condition and confirm it.
- Ask at most one question per reply.
- For vague phrases such as "good customers", "old customers", or "small
  stuff", do **one clear ask** for the missing numeric threshold in that turn.
  Do not invent, suggest, or recommend a number outside the presets (never
  "should it be 12 months?").
- Numbers the manager already stated (for example "within 14 days") are
  confirmed. Do not ask about them again.
- If the manager drops a qualifier from a rule, say plainly what the rule now
  covers (for example "every refund of $30 or less, for any reason") and ask
  once whether that wider rule is intended before building it.
- Offer presets in the question and mention the on-screen pick list:
  - amount ("small stuff"): $25, $30, $50, or custom
  - tenure ("good"/"old" customers): 6, 12, or 24 months, or custom
  - delivery window when needed: 7, 14, or 30 days, or custom
- If the manager's reply is still vague, ask **once more** with the same
  presets only; do not silently fill a value.
- Never silently invent numeric values. For every numeric value not explicitly
  stated or pick-list-confirmed by the manager, do not put it in a candidate.
- When your reply asks for one of those thresholds and the manager has not yet
  answered it, end the message with exactly one marker line (hidden UI hint):
  [[clarify:amount_usd_lte]] or [[clarify:tenure_months_gte]] or
  [[clarify:days_since_delivery_lte]]
  Use at most one marker per reply. Never add a marker when you are not
  asking a question, when the value is already confirmed, or when the manager
  dropped that requirement.

Tools:
- `query_history`: optional filters against the local synthetic history sample.
  Use it to see how many past requests match a draft rule sketch, spot edge
  cases, and ask better clarifying questions. Historical decisions are
  descriptive only — not proof that a rule is correct or safe.
- `build_policy`: after policy-affecting answers are clear, pass a complete
  candidate_policy based on the current session draft. Optionally pass
  assumptions and cannot_express. The host injects current_draft. For a clearly
  stated but unsupported requirement, put an explanation in cannot_express.
  Do not call build_policy while required answers remain unclear.
- If build_policy returns invalid/error, read the error, fix exactly what it
  names, and call build_policy again without narrating the failure to the
  manager. Only explain if it still fails.

After a tool result:
- status "proposed" means the local host may adopt the returned draft; summarize
  aggregate preview counts/totals/deltas only.
- status "invalid", "error", or "cannot_express" leaves the session draft
  unchanged; explain clearly.
- Never treat a proposal or history match as activation or a refund.
"""


def build_system_message(current_draft: Mapping[str, Any]) -> str:
    """Build the system message: instructions, dictionary, and current draft."""
    return "\n\n".join(
        [
            SYSTEM_INSTRUCTIONS,
            "Data dictionary (field meanings):",
            DATA_DICTIONARY_TEXT,
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
    max_tool_rounds: int = 6,
    on_text_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one manager turn with OpenAI tool calling.

    Session draft updates only on ``proposed``.

    Parameters
    ----------
    * :
        Everything after must be keyword-only.
    user_text :
        This turn's manager message (from Streamlit chat input).
    history :
        Prior chat turns as ``{"role", "content"}`` maps — not including the
        current ``user_text`` (the page passes ``messages[:-1]``).
    current_draft :
        Session policy JSON so far (starts from ``policy.template.json``).
    client :
        Optional OpenAI client; ``None`` creates one with ``OPENAI_API_KEY``
        (tests inject a mock).
    model :
        OpenAI model id; default ``"gpt-4o-mini"``.
    max_tool_rounds :
        Cap on model → tool → model loops so it cannot tool-call forever.
    on_text_delta :
        Optional callback; when given, replies are streamed and each text
        chunk is passed to it as it arrives (the page uses this to show text
        gradually). ``None`` uses a single non-streamed call.
    """
    draft = deepcopy(dict(current_draft))

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)

    # prepare system message + history + user message
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_message(draft)},
        *conversation_messages_for_provider(history),
        {"role": "user", "content": user_text},
    ]
    tools = registered_tool_schemas()

    tool_results: list[dict[str, Any]] = []
    preview_summary: dict[str, Any] | None = None
    draft_updated = False
    assistant_text = ""
    # Text from every round, so a reply written before a tool call is kept.
    spoken_parts: list[str] = []

    for _ in range(max_tool_rounds): # we limit the number of tool rounds to avoid infinite loops
        if on_text_delta is None:
            choice = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            ).choices[0].message
        else:
            choice = collect_streamed_message(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    stream=True,
                ),
                on_text_delta,
            )

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
        if (choice.content or "").strip():
            spoken_parts.append(choice.content.strip())
            if on_text_delta is not None:
                on_text_delta("\n\n")

        if not tool_calls:
            assistant_text = "\n\n".join(spoken_parts)
            break

        for call in tool_calls:
            result = execute_registered_tool(
                call.function.name, call.function.arguments, draft
            )
            tool_results.append(result)
            if call.function.name == BUILD_POLICY_TOOL_NAME:
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
                        tool_result_for_model(result), default=str
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
        assistant_text = assistant_text_from_tool_results(
            tool_results, preview_summary
        )

    return {
        "assistant_text": assistant_text or "(No assistant text returned.)",
        "draft": draft,
        "draft_updated": draft_updated,
        "preview_summary": preview_summary,
        "tool_results": tool_results,
        "provider_messages": messages,
    }

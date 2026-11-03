"""Manager policy-drafting agent: one LangGraph turn with local tools.

The model comes from LangChain, so OpenAI, Claude, or Gemini can drive the
same graph (see ``llm_provider``). Never approves refunds or activates policy.
History query is for the assignment demo on synthetic sample data; historical
decisions are not ground-truth labels.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Any, Callable, Mapping, Sequence, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from utils.policy.llm_provider import (
    PROVIDERS,
    llm_configured,
    make_chat_model,
    offline_message,
    provider_settings,
)
from utils.policy.policy_agent_utils import (
    BUILD_POLICY_TOOL_NAME,
    POLICIES_DIR,
    QUERY_HISTORY_TOOL_NAME,
    apply_build_policy_result,
    assert_messages_safe_for_provider,
    assistant_text_from_tool_results,
    build_policy_tool_schema,
    conversation_messages_for_provider,
    ensure_session_defaults,
    execute_build_policy_arguments,
    execute_registered_tool,
    format_preview_summary_for_manager,
    load_template_draft,
    policy_confirmation_blockers,
    policy_name_error,
    provider_payload_contains_forbidden_customer_data,
    query_history_tool_schema,
    registered_tool_names,
    registered_tool_schemas,
    save_policy_draft,
    suggest_policy_name,
    tool_result_for_model,
    unrequested_question_topics,
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

Closed policy shape: {version, status, comment, allow, review_notes, rule_notes}.
review_notes and rule_notes are optional. A rule has exactly
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
You may use review_notes and rule_notes. Do not invent other fields, operators, rails, reject actions, or partial refunds.

Sorting operating notes:
- The manager does not have to say that something is a note for reviewers.
  They may paste several operating notes at once. Split every sentence or
  clause first. One clause becomes one auto-approve rule or one reviewer
  note, not both.
- Ask before you build when a clause could be either, or when you are not
  sure which reason it belongs to. Use the same bar as a vague rule: one
  plain question, then wait. Do not guess.
- An auto-approve rule is only a clause that says the refund goes through
  without a person, and every condition is already in the closed schema.
  "Change of mind: we accept returns within 14 days of delivery. No
  exceptions." is a rule for changed_mind within 14 days. "No exceptions"
  does not add a condition.
- A reviewer note tells a person what to check, when a partial is their
  call, when to refuse, or states a cost or process that is not an automatic
  refund. Put it in review_notes as {"reason", "text"}. Keep that clause's
  wording in text. Do not rewrite it into a rule. A review note is not an auto-approve rule. It does not auto-approve, auto-reject, or change who is paid. There is no reject action.
- reason is one of the six cleaned reasons when the clause is clearly about
  only that reason. Damaged on arrival is arrived_damaged. Otherwise use
  general. "Never arrived: if the carrier has a signature or delivery photo,
  we reject unless the carrier opens an investigation." is a note with reason
  never_arrived, not a reject rule.
- "Damaged on arrival: we need a photo for anything over $50." is a note for
  arrived_damaged. The $50 stays inside the note. Do not turn it into an
  amount condition on a rule.
- "Fraud flag: goes to the fraud team. If they confirm the card was stolen,
  refund the real cardholder in full." is a general note. Fraud is not a
  stated reason. Do not write an allow rule that pays from that sentence.
  A raised fraud flag already goes to a person before any automatic rule.
  The note still changes what the draft should say. Ask once whether every
  automatic rule should require a clear fraud flag, including a change of
  mind within 14 days. After they say yes, add fraud_flag "no" to each allow
  rule that does not already have it, and keep the sentence as a review note.
  If they say no, keep the note and do not add the condition. Do not add it
  before they answer.
- A note about one automatic rule is stored as rule_notes: {"id", "text"}.
  id is that rule's id. text is the manager's words. It does not change who
  is auto-approved. Example: the wrong-item rule refunds in full, and the
  manager says that if support shows the item matches the order line, the
  customer got what they ordered. Store that sentence on the wrong-item
  rule. Do not turn those words into an allow condition. If you are not sure
  which rule the note belongs to, ask one question and do not guess an id.
- "Every chargeback costs us the order amount plus a $25 fee, and too many
  put our card processing at risk." is a general note. It names no refund
  reason.
- "Small stuff from good customers goes through automatically. IT set that up
  last year." sounds like a rule, but small and good customers are not
  defined. Ask, as you already do for those phrases. Do not invent the amount
  or the tenure, and do not store that sentence as a note that pays.
- Split compound notes. "Our mistake (wrong item, picking error): refund in
  full, no questions asked. Partials for cosmetic damage or late delivery
  are the reviewer's call." is four pieces:
  - Wrong item, refund in full, no questions asked: this may be a rule for
    wrong_item. If you are not sure they want it automatic, ask.
  - Picking error is not a stated reason. Ask whether that part is a
    reviewer note. Do not map picking error to wrong_item.
  - Partials for cosmetic damage are the reviewer's call: a note. Cosmetic
    damage is not its own reason. Ask if they mean arrived damaged before
    you store it as arrived_damaged. If they do not confirm, use general.
  - Partials for late delivery are the reviewer's call: a separate note
    with reason late_delivery.
- Clear clauses can go in the same candidate as the rules. Leave unclear
  clauses out of that candidate and ask about one of them.
- When the manager was only adding notes, keep the current allow list
  unchanged.

How to write rules (the validator rejects anything else):
- Keep "version" from the current draft and set "status" to exactly "draft".
- Conditions inside one "when" are AND. Separate rules are OR.
- stated_reason takes exactly one string, never a list. For more than one
  reason, write one rule per reason.
- Each rule id is unique, short, and snake_case.
- Example: {"id": "changed_mind_14d", "action": "approve_full", "all_of":
  [{"when": {"stated_reason": "changed_mind", "days_since_delivery_lte": 14}}]}
- A phrase is a stated reason only when the data dictionary uses those words,
  ignoring spacing and case, or when the history sample itself spells it that
  way. wrong item is wrong_item. arrived damaged and damaged are
  arrived_damaged. late is late_delivery. Do not add synonyms. Picking error
  is not a stated reason; it appears in support notes. Lost is not a stated
  reason unless they confirm they mean never arrived. If nothing in the
  dictionary matches, say so instead of picking a close reason.

How you talk to the manager:
- The manager is not technical. Use plain business language only.
- Never show policy JSON, rule ids, field names (such as amount_usd_lte or
  stated_reason), operators, "all_of", or a "policy structure" breakdown. The
  page already shows the draft separately.
- Do not add fraud, a blank note, customer id, or already paid onto a rule
  unless the manager confirmed that a note they gave should stop that
  automatic rule. In the chat, say standard safety checks still apply. Do
  not show rule ids.
- When every answer is clear, call build_policy right away to check the
  policy. Do not announce it first and do not summarize before it passes.
- Only after build_policy returns "proposed" in this reply: give a short
  plain-language summary of the full policy (one bullet per auto-approve
  rule) and, separately, any reviewer notes in the manager's own words.
  Do not show the manager the JSON field name for those notes. Also say what
  the preview shows. If history_evidence shows something the manager should
  know (see "Deciding what to ask"), mention it in one sentence with the
  numbers. Then ask whether this is what they want. Stop there.
  Do not ask for a name and do not say you are saving. The page asks for a
  name only after the manager agrees, and the page does the saving.

Light chain of thought (keep short, plain language):
- Before asking a question or calling a tool, open with at most 3 short
  bullets: what you understood, what is still unclear, and one edge case
  worth checking. No technical terms.

Deciding what to ask:
- Ask when a clause could be an automatic refund or a note for a person,
  when the reason is unclear, when a number is missing, or when the history
  data shows a real risk about the rule they described. Still one question
  per reply.
- Do not add conditions the manager did not mention. If they say "change of
  mind within 14 days", do not ask about a refund amount, how long someone
  has been a customer, or a delivery window unless they brought that up or
  the data gives a concrete reason.
- When their words are unclear, do not guess and do not ask a bare "what do
  you mean?". Use the data dictionary to find the fields those words could
  refer to, call query_history for each plausible reading, and ask which
  reading they meant. Quote the counts. Past decisions are context, not proof.
- Before building a rule that is already clear, you may call query_history
  with that rule's conditions to see what it would have covered. Raise a
  question from the data only when the numbers show a real risk or edge case,
  and quote them. For example: most matches are far above the typical amount,
  many past requests like these were rejected by reviewers, or the rule covers
  a reason the manager did not name.
- If nothing is vague and the data shows nothing notable, build right away.
- If they describe something no dictionary field can express, say so plainly.
  Do not invent a field to make it fit.

Clarification before build_policy:
- Ask in business terms. The manager may not know schema field names; do not
  ask them to pick columns or operators.
- Ask at most one question per reply.
- Do not decide a vague phrase on your own, and do not keep a private list of
  what words mean. "Good customers" and "old customers" are not automatically
  a tenure rule. "Small stuff" is not automatically a dollar cap or item size.
- For a vague phrase, look it up, then do **one clear ask**. Read the data
  dictionary for fields the words could refer to. Call query_history for each
  plausible reading and use those counts in the question. Name only readings
  the dictionary supports. If it has no match, say so. Do not offer a number,
  and do not add a marker, until they confirm the meaning.
- Example: they say "small stuff". The dictionary has a refund amount and a
  size reason. Ask which they mean, and quote what history shows for low
  amounts and for the size reason. Do not pick one.
- Numbers the manager already stated (for example "within 14 days") are
  confirmed. Do not ask about them again.
- If the manager drops a qualifier from a rule, say plainly what the rule now
  covers (for example "every refund of $30 or less, for any reason") and ask
  once whether that wider rule is intended before building it.
- Only after the meaning is confirmed and the number is still missing, choose
  2 to 4 whole-number choices for the on-screen pick list. Use a number the
  manager already said, or round amounts you saw in query_history for this
  question. The visible question and the marker must use the same choices.
- Do not put any of those numbers into a policy until the manager picks one
  or types a custom value.
- If the manager's reply is still vague, ask **once more**. Do not silently
  fill a meaning or a value.
- Never silently invent numeric values. For every numeric value not explicitly
  stated or pick-list-confirmed by the manager, do not put it in a candidate.
- When your reply asks for a confirmed threshold and the manager has not yet
  answered it, end the message with exactly one marker line (hidden UI hint).
  The topic is a short plain phrase for what the number decides. The values
  are the same choices, with no dollar sign:
  [[clarify:amount_usd_lte|Small stuff from good customers|25,30,50]]
  [[clarify:tenure_months_gte|how long they have been a customer|6,12,24]]
  [[clarify:days_since_delivery_lte|days since delivery|7,14,30]]
  The page shows that topic and those buttons, plus a custom number.
  Use at most one marker per reply. Never add a marker when you are asking
  what a vague phrase means, when you are not asking a question, when the
  value is already confirmed, or when the manager dropped that requirement.
- If two or more thresholds are missing, ask only for the first one now and
  the next one after the manager answers.
- Example, only after they said small stuff means the refund amount (marker
  on its own last line):
  For small stuff from good customers, which amount should count: $25, $30, $50, or another amount?
  [[clarify:amount_usd_lte|Small stuff from good customers|25,30,50]]

Tools:
- `query_history`: optional filters against the local synthetic history sample.
  Use it to see how many past requests match a draft rule sketch, spot edge
  cases, and ask better clarifying questions. Historical decisions are
  descriptive only — not proof that a rule is correct or safe.
- build_policy's "proposed" result also includes history_evidence: how many
  past requests the rule would have matched, their amount range, and what
  reviewers decided then. Use it to decide whether anything is worth raising.
- `build_policy`: validates and previews a complete candidate_policy based on
  the current session draft. Optionally pass assumptions and cannot_express.
  The host injects current_draft. For a clearly stated but unsupported
  requirement, put an explanation in cannot_express. Do not call it while
  required answers remain unclear.
- If build_policy returns invalid/error, read the error, fix exactly what it
  names, and call build_policy again without narrating the failure to the
  manager. If the fix needs a decision only the manager can make, ask one
  plain-language question instead.

After a tool result:
- status "proposed" means the local host may adopt the returned draft; summarize
  aggregate preview counts/totals/deltas only. Mention reviewer notes
  separately from auto-approve rules, in the manager's words, and do not show
  the manager the JSON field name.
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


class AgentState(TypedDict):
    """State that flows through the agent graph for one manager turn."""

    messages: Annotated[list[AnyMessage], add_messages]
    draft: dict[str, Any]
    draft_updated: bool
    preview_summary: dict[str, Any] | None
    tool_results: list[dict[str, Any]]
    manager_texts: list[str]
    guard_used: bool


def _guard_note(topics: Sequence[str]) -> str:
    return (
        "[Host check, not from the manager] Your last reply asked about "
        f"{' and '.join(topics)}, which the manager did not mention. Do not add "
        "conditions they did not ask for. If their words are unclear, use the "
        "data dictionary and query_history to find the plausible readings, then "
        "ask which one they mean. Do not offer a number until they confirm it. "
        "If their request is clear, call build_policy now."
    )


def build_agent_graph(chat_model: Any) -> Any:
    """Compile the agent loop: model → (tools → model)* → guard → end.

    The model only proposes tool calls. The ``tools`` node runs them in local
    Python and is the only place the session draft can change, and only on a
    ``proposed`` build result. The ``guard`` node checks the final reply in
    code: if it asks about a topic the manager never raised, the reply is
    dropped and the model gets one retry with a host note.
    """
    model_with_tools = chat_model.bind_tools(registered_tool_schemas())

    def call_model(state: AgentState) -> dict[str, Any]:
        return {"messages": [model_with_tools.invoke(state["messages"])]}

    def run_tools(state: AgentState) -> dict[str, Any]:
        draft = state["draft"]
        draft_updated = state["draft_updated"]
        preview_summary = state["preview_summary"]
        tool_results = list(state["tool_results"])
        replies: list[ToolMessage] = []
        for call in state["messages"][-1].tool_calls:
            result = execute_registered_tool(call["name"], call["args"], draft)
            tool_results.append(result)
            if call["name"] == BUILD_POLICY_TOOL_NAME:
                next_draft, changed = apply_build_policy_result(draft, result)
                if changed:
                    draft = next_draft
                    draft_updated = True
                    preview_summary = result.get("preview_summary")
            replies.append(
                ToolMessage(
                    content=json.dumps(tool_result_for_model(result), default=str),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
        return {
            "messages": replies,
            "draft": draft,
            "draft_updated": draft_updated,
            "preview_summary": preview_summary,
            "tool_results": tool_results,
        }

    def guard_reply(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        if state["guard_used"]:
            return {}
        topics = unrequested_question_topics(last.text, state["manager_texts"])
        if not topics:
            return {}
        return {
            "messages": [RemoveMessage(id=last.id), HumanMessage(_guard_note(topics))],
            "guard_used": True,
        }

    def after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "guard"

    def after_guard(state: AgentState) -> str:
        # The guard leaves a host note as the last message only when it fired.
        return "model" if isinstance(state["messages"][-1], HumanMessage) else END

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("guard", guard_reply)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, ["tools", "guard"])
    graph.add_edge("tools", "model")
    graph.add_conditional_edges("guard", after_guard, ["model", END])
    return graph.compile()


def _to_plain_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """Turn LangChain messages into plain dicts for logging and safety checks."""
    plain: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.type, "content": message.content}
        if getattr(message, "tool_calls", None):
            item["tool_calls"] = message.tool_calls
        if isinstance(message, ToolMessage):
            item["tool_call_id"] = message.tool_call_id
        plain.append(item)
    return plain


def run_policy_agent_turn(
    *,
    user_text: str,
    history: Sequence[Mapping[str, str]],
    current_draft: Mapping[str, Any],
    chat_model: Any | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    max_tool_rounds: int = 6,
    on_text_delta: Callable[[str], None] | None = None,
    on_text_reset: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run one manager turn through the LangGraph agent loop.

    Session draft updates only on ``proposed``.

    Parameters
    ----------
    * :
        Everything after must be keyword-only.
    user_text :
        This turn's manager message (from Streamlit chat input).
    history :
        Prior chat turns as ``{"role", "content"}`` maps — not including the
        current ``user_text``.
    current_draft :
        Session policy JSON so far (starts from ``policy.template.json``).
    chat_model :
        Optional LangChain chat model; ``None`` builds one from ``provider``
        and ``api_key`` (tests inject a fake model).
    provider :
        ``"openai"``, ``"anthropic"``, or ``"gemini"``; ``None`` uses
        ``LLM_PROVIDER`` (default ``openai``).
    api_key :
        Optional key; ``None`` uses the provider's environment variable.
    max_tool_rounds :
        Cap on model → tool → model loops so it cannot tool-call forever.
    on_text_delta :
        Optional callback; when given, each text chunk is passed to it as it
        arrives (the page uses this to show text gradually).
    on_text_reset :
        Optional callback; called when the guard drops a reply that was
        already streamed, so the page can clear it. The kept text is then
        sent again through ``on_text_delta``.
    """
    draft = deepcopy(dict(current_draft))
    if chat_model is None:
        chat_model = make_chat_model(provider, api_key=api_key)

    start_messages: list[AnyMessage] = [
        SystemMessage(build_system_message(draft)),
        *(
            HumanMessage(item["content"]) if item["role"] == "user" else AIMessage(item["content"])
            for item in conversation_messages_for_provider(history)
        ),
        HumanMessage(user_text),
    ]
    manager_texts = [
        item["content"]
        for item in conversation_messages_for_provider(history)
        if item["role"] == "user"
    ] + [user_text]
    initial: AgentState = {
        "messages": start_messages,
        "draft": draft,
        "draft_updated": False,
        "preview_summary": None,
        "tool_results": [],
        "manager_texts": manager_texts,
        "guard_used": False,
    }
    # Each round is a model step plus a tools step; the guard can add one
    # retry (guard + model), and the final reply and guard pass add two more.
    config = {"recursion_limit": 2 * max_tool_rounds + 4}
    graph = build_agent_graph(chat_model)

    final_state: dict[str, Any] = dict(initial)
    stopped_early = False
    try:
        if on_text_delta is None:
            final_state = graph.invoke(initial, config)
        else:
            current_id = None
            spoke = False
            for mode, payload in graph.stream(
                initial, config, stream_mode=["messages", "updates", "values"]
            ):
                if mode == "values":
                    final_state = payload
                    continue
                if mode == "updates":
                    if (payload.get("guard") or {}).get("messages"):
                        # The guard dropped the reply just shown; redraw what
                        # is kept so the screen matches the saved reply.
                        kept = [
                            message.text.strip()
                            for message in final_state["messages"][len(start_messages):-1]
                            if isinstance(message, AIMessage) and message.text.strip()
                        ]
                        if on_text_reset is not None:
                            on_text_reset()
                            spoke = bool(kept)
                            if kept:
                                on_text_delta("\n\n".join(kept))
                        current_id = None
                    continue
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "model":
                    continue
                text = chunk.text
                if not text:
                    continue
                # A new model message starts a new paragraph on screen.
                if chunk.id != current_id:
                    if spoke:
                        on_text_delta("\n\n")
                    current_id = chunk.id
                spoke = True
                on_text_delta(text)
    except GraphRecursionError:
        stopped_early = True

    turn_messages = final_state["messages"][len(start_messages):]
    spoken = [
        message.text.strip()
        for message in turn_messages
        if isinstance(message, AIMessage) and message.text.strip()
    ]
    if stopped_early:
        assistant_text = (
            "Stopped after the maximum number of tool rounds without a final "
            "assistant reply. The session draft was updated only if a proposed "
            "result was returned."
        )
    else:
        assistant_text = "\n\n".join(spoken) or assistant_text_from_tool_results(
            final_state["tool_results"], final_state["preview_summary"]
        )

    return {
        "assistant_text": assistant_text or "(No assistant text returned.)",
        "draft": final_state["draft"],
        "draft_updated": final_state["draft_updated"],
        "preview_summary": final_state["preview_summary"],
        "tool_results": final_state["tool_results"],
        "provider_messages": _to_plain_messages(final_state["messages"]),
    }

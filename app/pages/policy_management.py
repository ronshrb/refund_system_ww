"""Policy Management — conversational manager drafting with ``build_policy``.

Session state owns conversation messages and the current draft. A LangGraph
agent (OpenAI, Claude, or Gemini via LangChain) may call local tools. This
page does not approve refunds, activate or version policy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import streamlit as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.policy import policy_agent
from utils.policy.policy_agent_utils import (
    CLARIFY_MARKER_PATTERN,
    CLARIFY_PRESET_PATTERNS,
    CLARIFY_PRESET_VALUES,
)

_SESSION_MESSAGES_KEY = "policy_management_messages"
_SESSION_DRAFT_KEY = "policy_management_draft"
_SESSION_PREVIEW_KEY = "policy_management_preview_summary"
_SESSION_CONFIRMED_KEY = "policy_management_confirmed_thresholds"
_SESSION_PENDING_KEY = "policy_management_pending_turn"
_SESSION_NOTICE_KEY = "policy_management_save_notice"
_SESSION_PROPOSAL_KEY = "policy_management_proposal"
_SESSION_NAME_STEP_KEY = "policy_management_name_step"
_SESSION_SUGGESTED_NAME_KEY = "policy_management_suggested_name"
_SESSION_BLOCKER_KEY = "policy_management_blocker"
_READY_MARKER = "[[ready_to_build]]"
_PARTIAL_MARKER = re.compile(r"\[(\[[^\]]*\]?)?$")


def _quick_pick_presets(kind: str) -> list[tuple[str, int]]:
    """Quick-pick labels from the shared preset values.

    Labels stay "$25", "6 months", and "7 days".
    """
    values = CLARIFY_PRESET_VALUES[kind]
    if kind == "amount_usd_lte":
        return [(f"${value}", value) for value in values]
    if kind == "tenure_months_gte":
        return [(f"{value} months", value) for value in values]
    return [(f"{value} days", value) for value in values]


_THRESHOLD_LABELS = {
    "amount_usd_lte": "Maximum refund amount",
    "tenure_months_gte": "Minimum time as a customer",
    "days_since_delivery_lte": "Maximum days since delivery",
}


def _strip_clarify_markers(text: str) -> str:
    """Remove UI-only markers from assistant text shown to the manager."""
    return CLARIFY_MARKER_PATTERN.sub("", text).replace(_READY_MARKER, "").rstrip()

def _pending_clarify_kind(
    messages: list[dict[str, str]], confirmed: set[str]
) -> str | None:
    """Return the threshold the latest assistant message is asking for, if any.

    Uses the hidden marker when present. The model sometimes forgets it, so
    the preset list it was told to offer (e.g. "$25, $30, $50") also counts.
    Thresholds the manager already picked are skipped; if several are asked,
    the first one in the text wins.
    """
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        text = message.get("content") or ""
        found = [(m.start(), m.group(1)) for m in CLARIFY_MARKER_PATTERN.finditer(text)]
        for kind, pattern in CLARIFY_PRESET_PATTERNS.items():
            match = pattern.search(text)
            if match:
                found.append((match.start(), kind))
        for _, kind in sorted(found):
            if kind not in confirmed:
                return kind
        return None
    return None


def _markdown_safe(text: str) -> str:
    """Escape ``$`` so Streamlit does not render "$25, $30" as a math formula."""
    return text.replace("$", r"\$")


def _pick_list_reply(kind: str, value: int) -> str:
    """Build a plain-language user confirmation from a pick-list choice."""
    if kind == "amount_usd_lte":
        return f"Confirmed: refunds of ${value} or less."
    if kind == "tenure_months_gte":
        return f"Confirmed: customers for at least {value} months."
    return f"Confirmed: within {value} days of delivery."


def _render_user_text(text: str) -> None:
    """Show manager text with its line breaks (Markdown joins single newlines)."""
    st.markdown(_markdown_safe(text).replace("\n", "  \n"))


def _strip_streaming_text(text: str) -> str:
    """Hide full and half-received clarify markers while a reply streams in."""
    return _markdown_safe(_PARTIAL_MARKER.sub("", _strip_clarify_markers(text)))


def _queue_turn(user_text: str) -> None:
    """Queue a manager message; the next run renders and streams the reply."""
    st.session_state[_SESSION_PENDING_KEY] = user_text
    st.rerun()


def _confirm_pick(kind: str, value: int) -> None:
    """Record a pick so its Quick pick never reappears, then queue the turn."""
    st.session_state[_SESSION_CONFIRMED_KEY].add(kind)
    _queue_turn(_pick_list_reply(kind, value))


def _run_manager_turn(user_text: str) -> None:
    """Show the user turn, stream the agent reply, and update session state."""
    messages = st.session_state[_SESSION_MESSAGES_KEY]
    history_before = list(messages)
    previous_draft = st.session_state[_SESSION_DRAFT_KEY]
    previous_preview = st.session_state.get(_SESSION_PREVIEW_KEY)
    messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        _render_user_text(user_text)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_Thinking…_")
        streamed: list[str] = []

        def show_delta(delta: str) -> None:
            streamed.append(delta)
            placeholder.markdown(_strip_streaming_text("".join(streamed)) + "▌")

        def clear_streamed() -> None:
            streamed.clear()
            placeholder.markdown("_Thinking…_")

        turn = policy_agent.run_policy_agent_turn(
            user_text=user_text,
            history=history_before,
            current_draft=st.session_state[_SESSION_DRAFT_KEY],
            on_text_delta=show_delta,
            on_text_reset=clear_streamed,
        )

    st.session_state[_SESSION_DRAFT_KEY] = turn["draft"]
    if turn.get("preview_summary") is not None:
        st.session_state[_SESSION_PREVIEW_KEY] = turn["preview_summary"]

    messages.append({"role": "assistant", "content": turn["assistant_text"]})

    # The page, not the model, decides what happens after a build. A draft is
    # offered for approval only when every threshold in it was stated by the
    # manager. Otherwise the working draft stays as it was and a pick list
    # asks for the missing value.
    st.session_state[_SESSION_NAME_STEP_KEY] = False
    if not turn["draft_updated"]:
        st.session_state[_SESSION_PROPOSAL_KEY] = None
        st.session_state[_SESSION_BLOCKER_KEY] = None
        return

    blockers = policy_agent.policy_confirmation_blockers(
        turn["draft"], [item for item in messages if item["role"] == "user"]
    )
    if blockers:
        st.session_state[_SESSION_DRAFT_KEY] = previous_draft
        if previous_preview is None:
            st.session_state.pop(_SESSION_PREVIEW_KEY, None)
        else:
            st.session_state[_SESSION_PREVIEW_KEY] = previous_preview
        st.session_state[_SESSION_PROPOSAL_KEY] = None
        st.session_state[_SESSION_BLOCKER_KEY] = blockers[0]
        return

    st.session_state[_SESSION_PROPOSAL_KEY] = turn["draft"]
    st.session_state[_SESSION_BLOCKER_KEY] = None


def _save_proposal(name: str) -> None:
    """Write the approved draft to ``policies/<name>.json`` and hide Save."""
    try:
        policy_agent.save_policy_draft(name, st.session_state[_SESSION_PROPOSAL_KEY])
    except (OSError, ValueError) as error:
        st.session_state[_SESSION_NOTICE_KEY] = ("error", f"Not saved: {error}")
    else:
        st.session_state[_SESSION_PROPOSAL_KEY] = None
        st.session_state[_SESSION_NAME_STEP_KEY] = False
        st.session_state[_SESSION_NOTICE_KEY] = ("success", f"Saved “{name}” to policies")
    st.rerun()


policy_agent.ensure_session_defaults(
    st.session_state,
    messages_key=_SESSION_MESSAGES_KEY,
    draft_key=_SESSION_DRAFT_KEY,
    preview_key=_SESSION_PREVIEW_KEY,
)
st.session_state.setdefault(_SESSION_CONFIRMED_KEY, set())
st.session_state.setdefault(_SESSION_PROPOSAL_KEY, None)
st.session_state.setdefault(_SESSION_NAME_STEP_KEY, False)
st.session_state.setdefault(_SESSION_BLOCKER_KEY, None)

st.title("Policy Management")
st.caption("Draft and preview policy changes. Does not approve refunds.")

if policy_agent.llm_configured():
    st.caption(f"Model: {policy_agent.provider_settings()['label']}")
else:
    st.info(policy_agent.offline_message())

with st.expander("Current session draft", expanded=False):
    st.json(st.session_state[_SESSION_DRAFT_KEY])

preview = st.session_state.get(_SESSION_PREVIEW_KEY)
if preview:
    st.markdown(policy_agent.format_preview_summary_for_manager(preview))

messages = st.session_state[_SESSION_MESSAGES_KEY]
for message in messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            st.markdown(_markdown_safe(_strip_clarify_markers(message["content"])))
        else:
            _render_user_text(message["content"])

pending_text = st.session_state.pop(_SESSION_PENDING_KEY, None)
if pending_text:
    _run_manager_turn(pending_text)
    st.rerun()

notice = st.session_state.pop(_SESSION_NOTICE_KEY, None)
if notice:
    level, text = notice
    getattr(st, level)(text)

if st.session_state[_SESSION_PROPOSAL_KEY] is not None:
    proposal = st.session_state[_SESSION_PROPOSAL_KEY]
    if not st.session_state[_SESSION_NAME_STEP_KEY]:
        st.subheader("Does this policy look right?")
        yes_col, no_col = st.columns(2)
        if yes_col.button("Yes, save it", type="primary"):
            st.session_state[_SESSION_NAME_STEP_KEY] = True
            st.session_state[_SESSION_SUGGESTED_NAME_KEY] = (
                policy_agent.suggest_policy_name(proposal)
            )
            st.rerun()
        if no_col.button("I want to change it"):
            st.session_state[_SESSION_PROPOSAL_KEY] = None
            st.rerun()
    else:
        st.subheader("Save Policy")
        st.caption("Name this policy, then click Save. It will be stored under policies/.")
        name = st.text_input(
            "Policy name",
            value=st.session_state.get(_SESSION_SUGGESTED_NAME_KEY, ""),
            key=f"policy_name_{len(messages)}",
        ).strip()
        if st.button("Save", type="primary", key=f"policy_save_{len(messages)}"):
            name_error = policy_agent.policy_name_error(name)
            if name_error:
                st.error(name_error)
            else:
                _save_proposal(name)

blocker = st.session_state[_SESSION_BLOCKER_KEY]
clarify_kind = (
    blocker["kind"]
    if blocker
    else _pending_clarify_kind(messages, st.session_state[_SESSION_CONFIRMED_KEY])
)
if clarify_kind and policy_agent.llm_configured() and st.session_state[_SESSION_PROPOSAL_KEY] is None:
    st.subheader("Quick pick")
    if blocker:
        st.info(blocker["detail"])
    st.caption(
        f"{_THRESHOLD_LABELS[clarify_kind]}. Choose a preset or enter a custom "
        "value — one clear answer so we do not invent a threshold."
    )
    cols = st.columns(len(CLARIFY_PRESET_VALUES[clarify_kind]) + 1)
    for index, (label, value) in enumerate(_quick_pick_presets(clarify_kind)):
        if cols[index].button(label, key=f"clarify_preset_{clarify_kind}_{value}"):
            _confirm_pick(clarify_kind, value)

    with cols[-1]:
        custom = st.number_input(
            "Custom",
            min_value=0,
            step=1,
            value=0,
            key=f"clarify_custom_{clarify_kind}",
        )
        if st.button("Use custom", key=f"clarify_custom_btn_{clarify_kind}"):
            _confirm_pick(clarify_kind, int(custom))

prompt = st.chat_input("Describe a policy change…")
if prompt:
    _queue_turn(prompt)

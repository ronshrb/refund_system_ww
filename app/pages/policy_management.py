"""Policy Management — conversational manager drafting with ``build_policy``.

Session state owns conversation messages and the current draft. The OpenAI
tools API may call local tools. This page does not approve refunds, activate
or version policy.
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

_SESSION_MESSAGES_KEY = "policy_management_messages"
_SESSION_DRAFT_KEY = "policy_management_draft"
_SESSION_PREVIEW_KEY = "policy_management_preview_summary"
_SESSION_CONFIRMED_KEY = "policy_management_confirmed_thresholds"
_SESSION_PENDING_KEY = "policy_management_pending_user_text"
_CLARIFY_MARKER = re.compile(
    r"\[\[clarify:(amount_usd_lte|tenure_months_gte|days_since_delivery_lte)\]\]"
)
_PARTIAL_MARKER = re.compile(r"\[(\[[^\]]*\]?)?$")

_THRESHOLD_PRESETS: dict[str, list[tuple[str, int]]] = {
    "amount_usd_lte": [("$25", 25), ("$30", 30), ("$50", 50)],
    "tenure_months_gte": [
        ("6 months", 6),
        ("12 months", 12),
        ("24 months", 24),
    ],
    "days_since_delivery_lte": [
        ("7 days", 7),
        ("14 days", 14),
        ("30 days", 30),
    ],
}

_THRESHOLD_LABELS = {
    "amount_usd_lte": "Maximum amount for “small” refunds",
    "tenure_months_gte": "Minimum tenure for “good” / “old” customers",
    "days_since_delivery_lte": "Maximum days since delivery",
}


def _strip_clarify_markers(text: str) -> str:
    """Remove UI-only clarify markers from assistant text shown to the manager."""
    return _CLARIFY_MARKER.sub("", text).rstrip()


def _pending_clarify_kind(messages: list[dict[str, str]]) -> str | None:
    """Return the clarify field from the latest assistant message, if any."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        match = _CLARIFY_MARKER.search(message.get("content") or "")
        return match.group(1) if match else None
    return None


def _pick_list_reply(kind: str, value: int) -> str:
    """Build a plain-language user confirmation from a pick-list choice."""
    if kind == "amount_usd_lte":
        return f"Confirmed: “small stuff” means refunds of ${value} or less."
    if kind == "tenure_months_gte":
        return (
            f"Confirmed: “good/old customers” means customers for at least "
            f"{value} months."
        )
    return f"Confirmed: within {value} days of delivery."


def _render_user_text(text: str) -> None:
    """Show manager text with its line breaks (Markdown joins single newlines)."""
    st.markdown(text.replace("\n", "  \n"))


def _strip_streaming_text(text: str) -> str:
    """Hide full and half-received clarify markers while a reply streams in."""
    return _PARTIAL_MARKER.sub("", _strip_clarify_markers(text))


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

        turn = policy_agent.run_policy_agent_turn(
            user_text=user_text,
            history=history_before,
            current_draft=st.session_state[_SESSION_DRAFT_KEY],
            on_text_delta=show_delta,
        )

    st.session_state[_SESSION_DRAFT_KEY] = turn["draft"]
    if turn.get("preview_summary") is not None:
        st.session_state[_SESSION_PREVIEW_KEY] = turn["preview_summary"]

    messages.append({"role": "assistant", "content": turn["assistant_text"]})


policy_agent.ensure_session_defaults(
    st.session_state,
    messages_key=_SESSION_MESSAGES_KEY,
    draft_key=_SESSION_DRAFT_KEY,
    preview_key=_SESSION_PREVIEW_KEY,
)
st.session_state.setdefault(_SESSION_CONFIRMED_KEY, set())

st.title("Policy Management")
st.caption(
    "Draft and locally preview policy changes. Does not approve refunds or "
    "activate policy."
)

if not policy_agent.OPENAI_API_KEY:
    st.info(policy_agent.OFFLINE_MESSAGE)

with st.expander("Current session draft (not live)", expanded=False):
    st.json(st.session_state[_SESSION_DRAFT_KEY])

preview = st.session_state.get(_SESSION_PREVIEW_KEY)
if preview:
    st.markdown(policy_agent.format_preview_summary_for_manager(preview))

messages = st.session_state[_SESSION_MESSAGES_KEY]
for message in messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            st.markdown(_strip_clarify_markers(message["content"]))
        else:
            _render_user_text(message["content"])

pending_text = st.session_state.pop(_SESSION_PENDING_KEY, None)
if pending_text:
    _run_manager_turn(pending_text)
    st.rerun()

clarify_kind = _pending_clarify_kind(messages)
if clarify_kind in st.session_state[_SESSION_CONFIRMED_KEY]:
    clarify_kind = None
if clarify_kind and policy_agent.OPENAI_API_KEY:
    st.subheader("Quick pick")
    st.caption(
        f"{_THRESHOLD_LABELS[clarify_kind]}. Choose a preset or enter a custom "
        "value — one clear answer so we do not invent a threshold."
    )
    cols = st.columns(len(_THRESHOLD_PRESETS[clarify_kind]) + 1)
    for index, (label, value) in enumerate(_THRESHOLD_PRESETS[clarify_kind]):
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

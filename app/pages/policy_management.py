"""Policy Management — conversational manager drafting with ``build_policy``.

Session state owns conversation messages and the current draft. The OpenAI
tools API may call the local ``build_policy`` function. This page does not
approve refunds, activate or version policy, or send customer request rows or
customer/support note text to a provider.
"""

from __future__ import annotations

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

policy_agent.ensure_session_defaults(
    st.session_state,
    messages_key=_SESSION_MESSAGES_KEY,
    draft_key=_SESSION_DRAFT_KEY,
    preview_key=_SESSION_PREVIEW_KEY,
)

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
    st.markdown(
        policy_agent.format_preview_summary_for_manager(preview)
    )

messages = st.session_state[_SESSION_MESSAGES_KEY]
for message in messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Describe a policy change…")
if prompt:
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    history_before = list(messages[:-1])
    turn = policy_agent.run_policy_agent_turn(
        user_text=prompt,
        history=history_before,
        current_draft=st.session_state[_SESSION_DRAFT_KEY],
    )

    st.session_state[_SESSION_DRAFT_KEY] = turn["draft"]
    if turn.get("preview_summary") is not None:
        st.session_state[_SESSION_PREVIEW_KEY] = turn["preview_summary"]

    assistant_text = turn["assistant_text"]
    messages.append({"role": "assistant", "content": assistant_text})
    with st.chat_message("assistant"):
        st.markdown(assistant_text)
        if turn.get("preview_summary"):
            st.markdown(
                policy_agent.format_preview_summary_for_manager(
                    turn["preview_summary"]
                )
            )

"""Policy Management page — manager chatbot shell.

Conversation history lives in Streamlit session state only. This page must not
approve refunds, activate or write live policy, register ``build_policy``, or
send customer request rows/notes to any provider.
"""

from __future__ import annotations

import streamlit as st

_SESSION_MESSAGES_KEY = "policy_management_messages"


def _ensure_message_history() -> list[dict[str, str]]:
    """Initialize and return the session-scoped chat message list."""
    if _SESSION_MESSAGES_KEY not in st.session_state:
        st.session_state[_SESSION_MESSAGES_KEY] = []
    return st.session_state[_SESSION_MESSAGES_KEY]


st.title("Policy Management")
st.caption("Does not approve refunds or activate policy.")

messages = _ensure_message_history()

for message in messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Describe a policy change…")
if prompt:
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

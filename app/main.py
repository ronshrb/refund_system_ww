"""Refund System — Streamlit multipage entry.

Run from the repository root:

    streamlit run app/main.py

Or with the project venv:

    .\\.venv\\Scripts\\python.exe -m streamlit run app/main.py

This entry wires navigation and the model sidebar. Apply sends one short
prompt to check the key. It does not evaluate refunds, call
``build_policy``, or activate policy. A blank key still uses ``.env``.
Nothing typed here is written to disk. Review and the policy chat stay off
until that check succeeds for the current provider, model, and key.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Repo root on path so pages can import ``utils`` when Streamlit runs this entry.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st

from utils.policy.llm_provider import (
    ACCEPTED_SESSION_KEY,
    DEFAULT_PROVIDER,
    PROVIDERS,
    active_model_name,
    check_api_key,
    choice_signature,
    key_is_accepted,
    llm_configured,
    offline_message,
    provider_settings,
    set_runtime_llm,
)

st.set_page_config(
    page_title="Refund System",
    layout="wide",
)

_PROVIDER_ORDER = ("openai", "anthropic", "gemini")


def _model_sidebar() -> None:
    """Provider, key, and optional model name for Reviews and Policy Management.

    The key is a password field. It is stored in the Streamlit session and
    passed to the model client. It is not printed and not written to ``.env``.
    """
    names = [name for name in _PROVIDER_ORDER if name in PROVIDERS]
    default_index = names.index(DEFAULT_PROVIDER) if DEFAULT_PROVIDER in names else 0
    with st.sidebar:
        st.header("Model")
        select_kwargs: dict = {
            "format_func": lambda name: PROVIDERS[name]["label"],
            "key": "llm_provider_choice",
        }
        # After the first run the widget key owns the value.
        if "llm_provider_choice" not in st.session_state:
            select_kwargs["index"] = default_index
        provider = st.selectbox("Provider", names, **select_kwargs)
        api_key = st.text_input(
            "API key",
            type="password",
            key="llm_api_key_input",
            help="This session only. Leave blank to use the key in .env. The key is not saved.",
        )
        default_model = PROVIDERS[provider]["default_model"]
        model_name = st.text_input(
            "Model",
            key="llm_model_name_input",
            placeholder=default_model,
            help=f"Optional. Blank uses {default_model}.",
        )
        set_runtime_llm(provider=provider, model=model_name, api_key=api_key)
        _drop_review_caches_when_the_model_changes(provider, api_key)
        # A key in the box or in .env is not enough. Apply must get a reply.
        if st.button("Apply API key", disabled=not llm_configured(), key="llm_apply_key"):
            with st.spinner("Checking the API key…"):
                try:
                    signature = check_api_key()
                except ValueError as error:
                    st.session_state[ACCEPTED_SESSION_KEY] = None
                    st.session_state["llm_key_error"] = str(error)
                    st.session_state["llm_key_error_for"] = choice_signature() or ""
                else:
                    st.session_state[ACCEPTED_SESSION_KEY] = signature
                    st.session_state["llm_key_error"] = ""
                    st.session_state["llm_key_error_for"] = ""
        _show_key_status()


def _show_key_status() -> None:
    """Say whether Apply has passed for the provider, model, and key on screen."""
    error = st.session_state.get("llm_key_error") or ""
    error_for = st.session_state.get("llm_key_error_for")
    current = choice_signature() or ""
    if error and error_for == current:
        st.error(error)
    elif key_is_accepted(st.session_state):
        st.caption(
            f"{provider_settings()['label']}, model {active_model_name()}. "
            "Key check passed. Nothing here is written to disk."
        )
    elif llm_configured():
        st.caption("Apply the API key to turn on Review and the policy chat.")
    else:
        st.caption(offline_message())


def _drop_review_caches_when_the_model_changes(provider: str, api_key: str) -> None:
    """A new provider, model, or key must not reuse a checkup from the old one.

    The mark is a short hash so the raw key is not copied into a second
    session field. The password widget already holds the key.
    """
    typed = api_key.strip()
    mark = hashlib.sha256(typed.encode("utf-8")).hexdigest()[:12] if typed else "env"
    signature = (provider, active_model_name(), mark)
    if st.session_state.get("llm_choice_signature") == signature:
        return
    st.session_state["llm_choice_signature"] = signature
    st.session_state["review_agent_cache"] = {}
    st.session_state["review_summary_cache"] = {}


_model_sidebar()

reviews_page = st.Page(
    "pages/reviews.py",
    title="Reviews",
    default=True,
)
policy_page = st.Page(
    "pages/policy_management.py",
    title="Policy Management",
)

navigation = st.navigation([reviews_page, policy_page], position="top")
navigation.run()

"""Choose the chat model for the policy agent: OpenAI, Claude, or Gemini.

LangChain gives one chat-model interface across providers, so the agent code
does not change when the provider does. The provider comes from
``LLM_PROVIDER`` (default ``openai``) and the model from ``LLM_MODEL``
(default per provider). Keys come from each provider's usual environment
variable unless the caller passes ``api_key`` (for a later "enter your key"
step in the UI).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "langchain_provider": "openai",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "anthropic": {
        "label": "Claude (Anthropic)",
        "langchain_provider": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-haiku-4-5",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "langchain_provider": "google_genai",
        "key_env": "GOOGLE_API_KEY",
        "default_model": "gemini-2.5-flash",
    },
}

DEFAULT_PROVIDER = (os.environ.get("LLM_PROVIDER") or "openai").strip().lower()


def provider_settings(provider: str | None = None) -> dict[str, str]:
    """Return the settings for ``provider`` (default from ``LLM_PROVIDER``)."""
    name = (provider or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider {name!r}; use one of {sorted(PROVIDERS)}."
        )
    return {"name": name, **PROVIDERS[name]}


def resolve_api_key(provider: str | None = None, api_key: str | None = None) -> str:
    """Return the key to use: an explicit one, else the provider's env var."""
    if api_key and api_key.strip():
        return api_key.strip()
    return (os.environ.get(provider_settings(provider)["key_env"]) or "").strip()


def llm_configured(provider: str | None = None, api_key: str | None = None) -> bool:
    """Return whether the provider is known and has a key."""
    try:
        return bool(resolve_api_key(provider, api_key))
    except ValueError:
        return False


def offline_message(provider: str | None = None) -> str:
    """Explain how to enable the agent when no key is set."""
    try:
        settings = provider_settings(provider)
    except ValueError as error:
        return str(error)
    return (
        f"{settings['label']} is not configured. Set `{settings['key_env']}` in "
        "the repository-root `.env` file, or choose another provider with "
        f"`LLM_PROVIDER` ({', '.join(sorted(PROVIDERS))})."
    )


def make_chat_model(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> Any:
    """Create a LangChain chat model for the chosen provider.

    Temperature 0 keeps replies as repeatable as the provider allows.
    """
    settings = provider_settings(provider)
    key = resolve_api_key(settings["name"], api_key)
    if not key:
        raise ValueError(offline_message(settings["name"]))

    from langchain.chat_models import init_chat_model

    # LLM_MODEL belongs to LLM_PROVIDER; another provider uses its own default.
    env_model = os.environ.get("LLM_MODEL") if settings["name"] == DEFAULT_PROVIDER else None
    return init_chat_model(
        model or env_model or settings["default_model"],
        model_provider=settings["langchain_provider"],
        api_key=key,
        temperature=0,
    )

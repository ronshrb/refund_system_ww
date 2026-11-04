"""Choose the chat model for the policy agent: OpenAI, Claude, or Gemini.

LangChain gives one chat-model interface across providers, so the agent code
does not change when the provider does. The provider comes from
``LLM_PROVIDER`` (default ``openai``) and the model from ``LLM_MODEL``
(default per provider). Keys come from each provider's usual environment
variable unless the caller passes ``api_key``.

The Streamlit sidebar stores one session choice with ``set_runtime_llm``.
A typed key is used only for the provider selected in that session. It is
not written to ``.env``. A blank model name keeps that provider's default.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

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
ACCEPTED_SESSION_KEY = "llm_accepted_signature"
_CHECK_PROMPT = "Reply with the single word ok."

# Session choice from the sidebar. None means "use the environment".
_runtime_provider: str | None = None
_runtime_model: str | None = None
_runtime_api_key: str | None = None


def set_runtime_llm(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> None:
    """Remember a provider, model name, and key for this process.

    Blank values fall through to the environment. The key stays in memory.
    It is not written to ``.env`` or to a policy file. A later call with
    blanks clears the session choice.
    """
    global _runtime_provider, _runtime_model, _runtime_api_key
    _runtime_provider = _blank_to_none(provider)
    if _runtime_provider is not None:
        _runtime_provider = _runtime_provider.lower()
    _runtime_model = _blank_to_none(model)
    _runtime_api_key = _blank_to_none(api_key)


def clear_runtime_llm() -> None:
    """Drop the session choice so the environment is used again."""
    set_runtime_llm()


def _blank_to_none(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def provider_settings(provider: str | None = None) -> dict[str, str]:
    """Return the settings for ``provider`` (session choice, else ``LLM_PROVIDER``)."""
    name = (provider or _runtime_provider or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider {name!r}; use one of {sorted(PROVIDERS)}."
        )
    return {"name": name, **PROVIDERS[name]}


def resolve_api_key(provider: str | None = None, api_key: str | None = None) -> str:
    """Return the key to use: an explicit one, else the session key, else the env var.

    The session key applies only to the provider selected with it. A call
    that names a different provider still reads that provider's env var.
    """
    if api_key and api_key.strip():
        return api_key.strip()
    settings = provider_settings(provider)
    if (
        _runtime_api_key
        and _runtime_provider
        and settings["name"] == _runtime_provider
    ):
        return _runtime_api_key
    return (os.environ.get(settings["key_env"]) or "").strip()


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
        f"{settings['label']} is not configured. Enter an API key in the sidebar, "
        f"or set `{settings['key_env']}` in the repository-root `.env` file. "
        "Or choose another provider with "
        f"`LLM_PROVIDER` ({', '.join(sorted(PROVIDERS))})."
    )


def active_model_name(provider: str | None = None, model: str | None = None) -> str:
    """Return the model id ``make_chat_model`` would use, without creating a client.

    An explicit name wins. Otherwise the session name is used, and only for
    the provider it was typed with. ``LLM_MODEL`` applies only to the
    provider named by ``LLM_PROVIDER`` at startup, so a Gemini session does
    not inherit an OpenAI model id.
    """
    settings = provider_settings(provider)
    typed = model.strip() if isinstance(model, str) else ""
    if typed:
        return typed
    if (
        _runtime_model
        and _runtime_provider
        and settings["name"] == _runtime_provider
    ):
        return _runtime_model
    if settings["name"] == DEFAULT_PROVIDER:
        env_model = (os.environ.get("LLM_MODEL") or "").strip()
        if env_model:
            return env_model
    return settings["default_model"]


def choice_signature(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str | None:
    """Fingerprint the provider, model, and key that would be used.

    The key is a hash. The raw key is not in the result.
    """
    try:
        settings = provider_settings(provider)
    except ValueError:
        return None
    key = resolve_api_key(settings["name"], api_key)
    if not key:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    model_name = active_model_name(settings["name"], model)
    return f"{settings['name']}|{model_name}|{digest}"


def key_is_accepted(store: Mapping) -> bool:
    """Return whether this session already passed a key check for the current choice."""
    signature = choice_signature()
    saved = store.get(ACCEPTED_SESSION_KEY) if isinstance(store, Mapping) else None
    return bool(signature) and saved == signature


def check_api_key(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    chat_model: Any | None = None,
) -> str:
    """Send one short prompt and return the choice fingerprint when it replies.

    No customer text is sent. A missing key raises before any call. Provider
    errors are replaced with a short reason that does not include the key.
    """
    signature = choice_signature(provider, model, api_key)
    if signature is None:
        raise ValueError(offline_message(provider))
    client = chat_model
    if client is None:
        try:
            client = make_chat_model(provider, model, api_key)
        except ModuleNotFoundError as error:
            raise ValueError("That provider is not installed.") from error
        except Exception as error:
            raise ValueError(_safe_provider_error(error)) from error
    try:
        reply = client.invoke(_CHECK_PROMPT)
    except Exception as error:
        raise ValueError(_safe_provider_error(error)) from error
    if not _reply_text(reply).strip():
        raise ValueError("The model returned an empty reply.")
    return signature


def _reply_text(reply: Any) -> str:
    content = getattr(reply, "content", reply)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _safe_provider_error(error: Exception) -> str:
    """A reason safe to show. Never the exception text, which may contain the key."""
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    if status in (401, 403):
        return "The API key was rejected."
    if status == 404:
        return "That model name was not accepted."
    if status == 429:
        return "The provider rate-limited the check. Try again in a moment."
    name = type(error).__name__.lower()
    if "auth" in name or "permission" in name:
        return "The API key was rejected."
    if "notfound" in name or "not_found" in name:
        return "That model name was not accepted."
    return "The key check failed. Check the provider, the model name, and the key."


def make_chat_model(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> Any:
    """Create a LangChain chat model for the chosen provider.

    Temperature 0 keeps replies as repeatable as the provider allows.
    ``model`` is the provider's model id, for example ``gpt-4o-mini``.
    A blank name uses :func:`active_model_name`.
    """
    settings = provider_settings(provider)
    key = resolve_api_key(settings["name"], api_key)
    if not key:
        raise ValueError(offline_message(settings["name"]))

    from langchain.chat_models import init_chat_model

    return init_chat_model(
        active_model_name(settings["name"], model),
        model_provider=settings["langchain_provider"],
        api_key=key,
        temperature=0,
    )

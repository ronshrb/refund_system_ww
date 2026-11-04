"""Session provider, model name, and API key. No provider is called."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from utils.policy.llm_provider import (
    ACCEPTED_SESSION_KEY,
    DEFAULT_PROVIDER,
    active_model_name,
    blank_model_name,
    check_api_key,
    clear_runtime_llm,
    key_is_accepted,
    llm_configured,
    offline_message,
    provider_settings,
    PROVIDERS,
    resolve_api_key,
    set_runtime_llm,
)


class RuntimeLlmTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_runtime_llm()

    def test_typed_key_stays_on_the_selected_provider(self) -> None:
        set_runtime_llm(provider="anthropic", model="claude-haiku-4-5", api_key="sidebar-test-key")
        self.assertEqual(provider_settings()["name"], "anthropic")
        self.assertEqual(resolve_api_key(), "sidebar-test-key")
        self.assertTrue(llm_configured())
        self.assertEqual(active_model_name(), "claude-haiku-4-5")
        self.assertNotEqual(resolve_api_key("openai"), "sidebar-test-key")
        self.assertNotEqual(resolve_api_key("gemini"), "sidebar-test-key")

    def test_blank_model_uses_that_providers_default(self) -> None:
        set_runtime_llm(provider="Gemini", model="  ", api_key="sidebar-test-key")
        self.assertEqual(provider_settings()["name"], "gemini")
        self.assertEqual(active_model_name(), "gemini-2.5-flash")

    def test_env_model_does_not_cross_to_another_provider(self) -> None:
        with mock.patch("utils.policy.llm_provider.DEFAULT_PROVIDER", "openai"):
            with mock.patch.dict(os.environ, {"LLM_MODEL": "gpt-4o"}, clear=False):
                set_runtime_llm(provider="gemini", api_key="sidebar-test-key")
                self.assertEqual(active_model_name(), "gemini-2.5-flash")
                self.assertEqual(active_model_name(model="gemini-2.5-pro"), "gemini-2.5-pro")

    def test_explicit_key_beats_the_session_key(self) -> None:
        set_runtime_llm(provider="openai", api_key="sidebar-test-key")
        self.assertEqual(resolve_api_key(api_key="call-key"), "call-key")

    def test_offline_message_names_provider_key_and_model(self) -> None:
        for name, settings in PROVIDERS.items():
            text = offline_message(name)
            self.assertIn(f"LLM_PROVIDER={name}", text)
            self.assertIn(settings["key_env"], text)
            self.assertIn("LLM_MODEL", text)
            self.assertIn(settings["default_model"], text)

    def test_blank_sidebar_reads_provider_key_and_model_from_the_environment(self) -> None:
        with mock.patch("utils.policy.llm_provider.DEFAULT_PROVIDER", "gemini"):
            with mock.patch.dict(
                os.environ,
                {
                    "LLM_MODEL": "gemini-2.5-pro",
                    "GOOGLE_API_KEY": "env-gemini-key",
                    "OPENAI_API_KEY": "env-openai-key",
                },
                clear=False,
            ):
                set_runtime_llm(provider="gemini", model="  ", api_key="  ")
                self.assertEqual(provider_settings()["name"], "gemini")
                self.assertEqual(resolve_api_key(), "env-gemini-key")
                self.assertEqual(blank_model_name(), "gemini-2.5-pro")
                self.assertEqual(active_model_name(), "gemini-2.5-pro")
                self.assertNotEqual(resolve_api_key("openai"), "env-gemini-key")

    def test_clear_drops_the_session_choice(self) -> None:
        before = active_model_name()
        set_runtime_llm(provider="gemini", model="gemini-2.5-pro", api_key="sidebar-test-key")
        self.assertEqual(active_model_name(), "gemini-2.5-pro")
        clear_runtime_llm()
        self.assertEqual(provider_settings()["name"], DEFAULT_PROVIDER)
        self.assertEqual(active_model_name(), before)
        self.assertNotEqual(resolve_api_key(), "sidebar-test-key")


class KeyCheckTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_runtime_llm()

    def test_a_reply_accepts_the_choice_without_storing_the_key(self) -> None:
        set_runtime_llm(provider="openai", model="gpt-4o-mini", api_key="sidebar-test-key")

        class Ok:
            def invoke(self, prompt: str):
                self.prompt = prompt
                return type("Reply", (), {"content": "ok"})()

        model = Ok()
        signature = check_api_key(chat_model=model)
        self.assertEqual(model.prompt, "Reply with the single word ok.")
        self.assertNotIn("sidebar-test-key", signature)
        store = {ACCEPTED_SESSION_KEY: signature}
        self.assertTrue(key_is_accepted(store))
        set_runtime_llm(provider="openai", model="gpt-4o-mini", api_key="other-key")
        self.assertFalse(key_is_accepted(store))

    def test_a_rejected_key_is_not_repeated_in_the_error(self) -> None:
        set_runtime_llm(provider="openai", api_key="sidebar-test-key")

        class Bad:
            def invoke(self, prompt: str):
                error = RuntimeError("bad key sidebar-test-key")
                error.status_code = 401
                raise error

        with self.assertRaises(ValueError) as caught:
            check_api_key(chat_model=Bad())
        message = str(caught.exception)
        self.assertNotIn("sidebar-test-key", message)
        self.assertIn("rejected", message.lower())

    def test_an_empty_reply_does_not_count_as_a_working_key(self) -> None:
        set_runtime_llm(provider="openai", api_key="sidebar-test-key")

        class Empty:
            def invoke(self, prompt: str):
                return type("Reply", (), {"content": "  "})()

        with self.assertRaises(ValueError) as caught:
            check_api_key(chat_model=Empty())
        self.assertIn("empty", str(caught.exception).lower())

    def test_review_and_chat_wait_for_an_accepted_key(self) -> None:
        root = Path(__file__).resolve().parents[1]
        reviews = (root / "app" / "pages" / "reviews.py").read_text(encoding="utf-8")
        builder = (root / "app" / "pages" / "policy_management.py").read_text(encoding="utf-8")
        sidebar = (root / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('st.button("Review", disabled=_stored is None or not _key_ready)', reviews)
        self.assertIn('st.chat_input("Describe a policy change…", disabled=not _key_ready)', builder)
        self.assertIn('st.button("Apply API key"', sidebar)
        self.assertIn("LLM_MODEL", sidebar)
        self.assertNotIn("already has an API key", sidebar)


if __name__ == "__main__":
    unittest.main()

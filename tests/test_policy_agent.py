"""Offline tests for the Streamlit manager policy-agent host.

Never calls the real OpenAI API. Clarification-before-tool is covered by
asserting system-instruction guidance (the host always registers
``build_policy``; the model is instructed not to call it while ambiguous).
"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from utils.policy import policy_agent


def _template_draft():
    return policy_agent.load_template_draft()


def _proposed_result(draft):
    return {
        "status": "proposed",
        "draft": draft,
        "assumptions": [],
        "preview_summary": {
            "prior": {"approved_count": 0, "approved_total_usd": "0.00"},
            "candidate": {"approved_count": 1, "approved_total_usd": "10.00"},
            "delta": {"approved_count": 1, "approved_total_usd": "10.00"},
        },
    }


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class PolicyAgentHostTests(unittest.TestCase):
    def test_tool_registration_shape_for_build_policy_only(self):
        schema = policy_agent.build_policy_tool_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "build_policy")
        self.assertEqual(policy_agent.registered_tool_names(), ("build_policy",))
        params = schema["function"]["parameters"]
        self.assertIn("candidate_policy", params["properties"])
        self.assertIn("candidate_policy", params["required"])
        # Host injects current_draft; it must not be a model-writable property.
        self.assertNotIn("current_draft", params["properties"])

    def test_clarification_guidance_encoded_in_system_instructions(self):
        """Clarification-before-tool is prompt/system guidance (documented)."""
        self.assertTrue(
            policy_agent.clarification_guidance_present(
                policy_agent.SYSTEM_INSTRUCTIONS
            )
        )
        system = policy_agent.build_system_message(_template_draft())
        self.assertTrue(policy_agent.clarification_guidance_present(system))
        collapsed = " ".join(system.split())
        self.assertIn("Do not invent an unconfirmed threshold", collapsed)
        self.assertIn("good customers", collapsed)
        self.assertIn("small stuff", collapsed)

    def test_session_draft_updates_only_on_proposed(self):
        original = _template_draft()
        candidate = deepcopy(original)
        candidate["comment"] = "candidate"
        proposed = _proposed_result(candidate)
        updated, changed = policy_agent.apply_build_policy_result(original, proposed)
        self.assertTrue(changed)
        self.assertEqual(updated["comment"], "candidate")

        for status in ("invalid", "error", "cannot_express"):
            preserved, changed = policy_agent.apply_build_policy_result(
                original,
                {"status": status, "errors": ["x"], "cannot_express": ["y"]},
            )
            self.assertFalse(changed)
            self.assertEqual(preserved, original)

    def test_invalid_and_cannot_express_preserve_draft_via_execute(self):
        current = _template_draft()
        before = deepcopy(current)

        invalid = policy_agent.execute_build_policy_arguments(
            {
                "candidate_policy": {
                    "version": 1,
                    "status": "active",
                    "comment": "bad",
                    "allow": [],
                }
            },
            current,
        )
        self.assertEqual(invalid["status"], "invalid")
        next_draft, changed = policy_agent.apply_build_policy_result(current, invalid)
        self.assertFalse(changed)
        self.assertEqual(next_draft, before)
        self.assertEqual(current, before)

        cannot = policy_agent.execute_build_policy_arguments(
            {
                "candidate_policy": current,
                "cannot_express": ["Carrier photo evidence unsupported."],
            },
            current,
        )
        self.assertEqual(cannot["status"], "cannot_express")
        next_draft, changed = policy_agent.apply_build_policy_result(current, cannot)
        self.assertFalse(changed)
        self.assertEqual(next_draft, before)

    def test_missing_key_path_is_honest_offline(self):
        with patch.object(policy_agent, "OPENAI_API_KEY", ""):
            turn = policy_agent.run_policy_agent_turn(
                user_text="Approve small refunds automatically",
                history=[],
                current_draft=_template_draft(),
            )
        self.assertTrue(turn["offline"])
        self.assertEqual(turn["assistant_text"], policy_agent.OFFLINE_MESSAGE)
        self.assertIn("does not invent a model reply", turn["assistant_text"])
        self.assertFalse(turn["draft_updated"])
        self.assertIsNone(turn["provider_messages"])

    def test_provider_payload_excludes_customer_fields_and_request_rows(self):
        secret_request_id = "RF-SECRET-9999"
        secret_customer_note = "SECRET_CUSTOMER_NOTE_VALUE_XYZ"
        secret_support_note = "SECRET_SUPPORT_NOTE_VALUE_XYZ"
        forbidden = (
            secret_request_id,
            secret_customer_note,
            secret_support_note,
            "PRIVATE-PENDING-ROW",
        )

        draft = _template_draft()
        system = policy_agent.build_system_message(draft)
        for marker in forbidden:
            self.assertNotIn(marker, system)

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "Allow changed_mind under $25 within 14 days.",
            },
        ]
        policy_agent.assert_messages_safe_for_provider(
            messages, forbidden_substrings=forbidden
        )
        self.assertFalse(
            policy_agent.provider_payload_contains_forbidden_customer_data(
                messages, forbidden_substrings=forbidden
            )
        )

        history = policy_agent.conversation_messages_for_provider(
            [
                {"role": "user", "content": "Raise the amount cap to 40"},
                {
                    "role": "assistant",
                    "content": "What amount_usd_lte threshold should I use?",
                },
            ]
        )
        blob = json.dumps(history)
        self.assertNotIn("customer_note", blob)
        self.assertNotIn("support_note", blob)
        self.assertNotIn(secret_request_id, blob)

    def test_mocked_openai_tool_call_updates_draft_on_proposed_only(self):
        current = _template_draft()
        candidate = deepcopy(current)
        candidate["comment"] = "manager candidate"
        candidate["allow"] = [
            {
                "id": "changed_mind_small",
                "action": "approve_full",
                "all_of": [
                    {
                        "when": {
                            "stated_reason": "changed_mind",
                            "amount_usd_lte": 25,
                        }
                    }
                ],
            }
        ]
        proposed = _proposed_result(candidate)

        tool_call = _FakeToolCall(
            "call_1",
            "build_policy",
            json.dumps({"candidate_policy": candidate, "assumptions": []}),
        )
        first = _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call]))
        second = _FakeResponse(
            _FakeMessage(
                content="Proposed a draft with aggregate preview only.",
                tool_calls=None,
            )
        )
        client = MagicMock()
        client.chat.completions.create.side_effect = [first, second]

        with patch.object(
            policy_agent, "OPENAI_API_KEY", "test-key"
        ), patch.object(
            policy_agent, "execute_build_policy_arguments", return_value=proposed
        ) as execute:
            turn = policy_agent.run_policy_agent_turn(
                user_text="changed_mind up to 25 dollars",
                history=[],
                current_draft=current,
                client=client,
            )

        execute.assert_called_once()
        args, kwargs = execute.call_args
        self.assertEqual(args[1], current)
        self.assertTrue(turn["draft_updated"])
        self.assertEqual(turn["draft"]["comment"], "manager candidate")
        self.assertEqual(
            turn["preview_summary"]["candidate"]["approved_total_usd"], "10.00"
        )
        self.assertFalse(turn["offline"])
        provider_blob = json.dumps(turn["provider_messages"], default=str)
        self.assertNotIn("RF-SECRET", provider_blob)
        self.assertNotIn("SECRET_CUSTOMER_NOTE", provider_blob)
        create_kwargs = client.chat.completions.create.call_args_list[0].kwargs
        self.assertEqual(
            create_kwargs["tools"][0]["function"]["name"], "build_policy"
        )
        self.assertEqual(len(create_kwargs["tools"]), 1)

    def test_mocked_cannot_express_preserves_draft_in_turn(self):
        current = _template_draft()
        before = deepcopy(current)
        tool_call = _FakeToolCall(
            "call_2",
            "build_policy",
            json.dumps(
                {
                    "candidate_policy": current,
                    "cannot_express": ["Reject action unsupported."],
                }
            ),
        )
        first = _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call]))
        second = _FakeResponse(
            _FakeMessage(content="Cannot express reject in schema.", tool_calls=None)
        )
        client = MagicMock()
        client.chat.completions.create.side_effect = [first, second]
        cannot = {
            "status": "cannot_express",
            "assumptions": [],
            "cannot_express": ["Reject action unsupported."],
        }

        with patch.object(
            policy_agent, "OPENAI_API_KEY", "test-key"
        ), patch.object(
            policy_agent, "execute_build_policy_arguments", return_value=cannot
        ):
            turn = policy_agent.run_policy_agent_turn(
                user_text="Auto-reject late delivery",
                history=[],
                current_draft=current,
                client=client,
            )

        self.assertFalse(turn["draft_updated"])
        self.assertEqual(turn["draft"], before)

    def test_ensure_session_defaults_initializes_template_draft(self):
        state: dict = {}
        policy_agent.ensure_session_defaults(state)
        self.assertEqual(state["policy_management_messages"], [])
        self.assertEqual(state["policy_management_draft"], _template_draft())
        self.assertIsNone(state["policy_management_preview_summary"])


if __name__ == "__main__":
    unittest.main()

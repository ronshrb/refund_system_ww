"""Offline tests for the Streamlit manager policy-agent host.

Never calls a real model provider; a scripted LangChain chat model drives the
LangGraph loop. Clarification-before-tool is covered by
asserting system-instruction guidance (the host always registers
``build_policy``; the model is instructed not to call it while ambiguous).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from utils.policy import policy_agent


def _template_draft():
    return policy_agent.load_template_draft()


_PROTECTED_POLICY_FILES = (
    Path(__file__).resolve().parents[1] / "data" / "raw" / "policy_notes.csv",
    Path(__file__).resolve().parents[1] / "policies" / "live_pointer.json",
)


def _protected_bytes():
    """Snapshot files this slice must not rewrite."""
    return {path: path.read_bytes() for path in _PROTECTED_POLICY_FILES}


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


class _ScriptedChatModel(BaseChatModel):
    """LangChain chat model that replays scripted replies, for offline tests.

    Records the tools it was bound to and the messages of every call.
    """

    replies: list[AIMessage]
    bound_tools: list[Any] = Field(default_factory=list)
    seen: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = list(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def _tool_call(name, args, call_id="call_1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class SavePolicyDraftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.policies_dir = Path(temp.name) / "policies"

    def _draft(self):
        draft = _template_draft()
        draft["allow"] = [
            {
                "id": "changed_mind_14d",
                "action": "approve_full",
                "all_of": [
                    {"when": {"stated_reason": "changed_mind", "days_since_delivery_lte": 14}}
                ],
            }
        ]
        return draft

    def test_saves_valid_draft_as_named_json(self):
        path = policy_agent.save_policy_draft("demo-1", self._draft(), self.policies_dir)
        self.assertEqual(path, self.policies_dir / "demo-1.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self._draft())

    def test_existing_name_is_rejected_and_file_kept(self):
        policy_agent.save_policy_draft("demo", self._draft(), self.policies_dir)
        self.assertIn("already exists", policy_agent.policy_name_error("demo", self.policies_dir))
        changed = self._draft()
        changed["comment"] = "second"
        with self.assertRaises(ValueError):
            policy_agent.save_policy_draft("demo", changed, self.policies_dir)
        saved = json.loads((self.policies_dir / "demo.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, self._draft())

    def test_bad_names_are_rejected(self):
        for name in ("", "Demo", "../escape", "a b", "-lead", "x" * 51):
            self.assertIsNotNone(policy_agent.policy_name_error(name, self.policies_dir), name)

    def test_invalid_or_non_draft_policy_is_not_saved(self):
        broken = self._draft()
        broken["allow"][0]["all_of"][0]["when"]["stated_reason"] = ["wrong_item"]
        live = self._draft()
        live["status"] = "live"
        for policy in (broken, live):
            with self.assertRaises(ValueError):
                policy_agent.save_policy_draft("demo", policy, self.policies_dir)
        self.assertFalse(self.policies_dir.exists())

    def test_saves_decimal_amount_as_json_number(self):
        from decimal import Decimal

        from utils.policy.policy_evaluator import policy_schema_errors

        draft = self._draft()
        draft["allow"][0]["all_of"][0]["when"]["amount_usd_lte"] = Decimal("49.50")
        path = policy_agent.save_policy_draft("decimal-cap", draft, self.policies_dir)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertIn("49.50", text)
        self.assertNotIn('"49.50"', text)
        loaded = json.loads(text)
        self.assertEqual(loaded["status"], "draft")
        self.assertEqual(policy_schema_errors(loaded), [])


class PolicyConfirmationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.policies_dir = Path(temp.name) / "policies"

    def _draft(self, when):
        draft = _template_draft()
        draft["allow"] = [
            {
                "id": "small_good_customers",
                "action": "approve_full",
                "all_of": [{"when": when}],
            }
        ]
        return draft

    def test_invented_tenure_blocks_and_stated_numbers_do_not(self):
        messages = [
            {"role": "user", "content": "Small stuff from good customers. Change of mind within 14 days."},
            {"role": "user", "content": "Confirmed: “small stuff” means refunds of $30 or less."},
        ]
        blocked = policy_agent.policy_confirmation_blockers(
            self._draft(
                {
                    "amount_usd_lte": 30,
                    "tenure_months_gte": 6,
                    "days_since_delivery_lte": 14,
                }
            ),
            messages,
        )
        self.assertEqual([item["kind"] for item in blocked], ["tenure_months_gte"])

        messages.append(
            {"role": "user", "content": "Confirmed: customers for at least 12 months."}
        )
        clear = policy_agent.policy_confirmation_blockers(
            self._draft(
                {
                    "amount_usd_lte": 30,
                    "tenure_months_gte": 12,
                    "days_since_delivery_lte": 14,
                }
            ),
            messages,
        )
        self.assertEqual(clear, [])

    def test_vague_phrases_do_not_require_a_threshold(self):
        draft = self._draft({"amount_usd_lte": 30})
        asked = [{"role": "user", "content": "Small stuff from good customers, $30."}]
        self.assertEqual(policy_agent.policy_confirmation_blockers(draft, asked), [])

    def test_suggested_name_comes_from_rule_ids_and_skips_taken_names(self):
        draft = self._draft({"amount_usd_lte": 30})
        self.assertEqual(
            policy_agent.suggest_policy_name(draft, self.policies_dir),
            "small-good-customers",
        )
        policy_agent.save_policy_draft("small-good-customers", draft, self.policies_dir)
        self.assertEqual(
            policy_agent.suggest_policy_name(draft, self.policies_dir),
            "small-good-customers-2",
        )


class PolicyAgentHostTests(unittest.TestCase):
    def test_tool_registration_includes_build_policy_and_query_history(self):
        names = policy_agent.registered_tool_names()
        self.assertEqual(names, ("build_policy", "query_history"))
        schemas = {
            item["function"]["name"]: item
            for item in policy_agent.registered_tool_schemas()
        }
        self.assertEqual(set(schemas), set(names))
        build = schemas["build_policy"]
        self.assertEqual(build["type"], "function")
        params = build["function"]["parameters"]
        self.assertIn("candidate_policy", params["properties"])
        self.assertIn("candidate_policy", params["required"])
        # Host injects current_draft; it must not be a model-writable property.
        self.assertNotIn("current_draft", params["properties"])
        history = schemas["query_history"]["function"]["parameters"]["properties"]
        self.assertIn("stated_reason", history)
        self.assertIn("amount_usd_lte", history)

    def test_clarification_guidance_encoded_in_system_instructions(self):
        """Clarification-before-tool is prompt/system guidance (documented)."""
        system = policy_agent.build_system_message(_template_draft())
        collapsed = " ".join(system.split())
        self.assertIn("Deciding what to ask", collapsed)
        self.assertIn("Do not add conditions the manager did not mention", collapsed)
        self.assertIn("history_evidence", collapsed)
        self.assertIn("one clear ask", collapsed)
        self.assertIn("[[clarify:tenure_months_gte|", collapsed)
        self.assertIn("are not automatically", collapsed)
        self.assertIn("Call query_history for each plausible reading", collapsed)
        self.assertIn("Picking error is not a stated reason", collapsed)
        self.assertNotIn("picking error -> wrong_item", collapsed)
        self.assertIn("Never silently invent numeric values", collapsed)
        self.assertIn("Light chain of thought", collapsed)
        self.assertIn("Data dictionary", collapsed)
        self.assertIn("tenure_months", collapsed)
        self.assertIn("query_history", collapsed)
        self.assertNotIn("Do not invent an unconfirmed threshold", collapsed)

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
        # System prompt has dictionary + draft only; no live request secrets.
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

        model = _ScriptedChatModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "build_policy",
                            {"candidate_policy": candidate, "assumptions": []},
                        )
                    ],
                ),
                AIMessage(content="Proposed a draft with aggregate preview only."),
            ]
        )

        with patch.object(
            policy_agent,
            "execute_registered_tool",
            return_value=proposed,
        ) as execute:
            turn = policy_agent.run_policy_agent_turn(
                user_text="changed_mind up to 25 dollars",
                history=[],
                current_draft=current,
                chat_model=model,
            )

        execute.assert_called_once()
        args, kwargs = execute.call_args
        self.assertEqual(args[0], "build_policy")
        self.assertEqual(args[1]["candidate_policy"], candidate)
        self.assertEqual(args[2], current)
        self.assertTrue(turn["draft_updated"])
        self.assertEqual(turn["draft"]["comment"], "manager candidate")
        self.assertEqual(
            turn["preview_summary"]["candidate"]["approved_total_usd"], "10.00"
        )
        self.assertEqual(
            turn["assistant_text"], "Proposed a draft with aggregate preview only."
        )
        provider_blob = json.dumps(turn["provider_messages"], default=str)
        self.assertNotIn("RF-SECRET", provider_blob)
        self.assertNotIn("SECRET_CUSTOMER_NOTE", provider_blob)
        self.assertEqual(
            [tool["function"]["name"] for tool in model.bound_tools],
            ["build_policy", "query_history"],
        )
        # The second model call sees the tool result for the first call.
        self.assertEqual(model.seen[1][-1].type, "tool")
        self.assertEqual(model.seen[1][-1].tool_call_id, "call_1")

    def test_streamed_turn_forwards_text_from_every_round(self):
        current = _template_draft()
        candidate = deepcopy(current)
        candidate["comment"] = "streamed candidate"
        model = _ScriptedChatModel(
            replies=[
                AIMessage(
                    content="Checking the numbers.",
                    tool_calls=[_tool_call("build_policy", {"candidate_policy": candidate})],
                ),
                AIMessage(content="Draft ready."),
            ]
        )
        deltas: list[str] = []

        with patch.object(
            policy_agent,
            "execute_registered_tool",
            return_value=_proposed_result(candidate),
        ):
            turn = policy_agent.run_policy_agent_turn(
                user_text="stream it",
                history=[],
                current_draft=current,
                chat_model=model,
                on_text_delta=deltas.append,
            )

        self.assertEqual(
            turn["assistant_text"], "Checking the numbers.\n\nDraft ready."
        )
        self.assertEqual("".join(deltas), turn["assistant_text"])
        self.assertEqual(turn["draft"]["comment"], "streamed candidate")

    def test_guard_drops_question_about_topic_manager_never_raised(self):
        model = _ScriptedChatModel(
            replies=[
                AIMessage(
                    content="Any amount limit? $25, $30, $50?\n[[clarify:amount_usd_lte]]"
                ),
                AIMessage(content="Building it now as you described."),
            ]
        )
        deltas: list[str] = []
        resets: list[bool] = []
        turn = policy_agent.run_policy_agent_turn(
            user_text="Change of mind: refund in full within 14 days of delivery.",
            history=[],
            current_draft=_template_draft(),
            chat_model=model,
            on_text_delta=deltas.append,
            on_text_reset=lambda: resets.append(True),
        )
        self.assertEqual(turn["assistant_text"], "Building it now as you described.")
        self.assertEqual(resets, [True])
        retry_prompt = model.seen[1][-1]
        self.assertEqual(retry_prompt.type, "human")
        self.assertIn("an amount limit", retry_prompt.content)
        self.assertNotIn("amount limit?", json.dumps(turn["provider_messages"], default=str))

    def test_guard_allows_question_about_topic_manager_raised_and_retries_once(self):
        jumped = _ScriptedChatModel(
            replies=[
                AIMessage(content="What counts as small stuff: $25, $30, $50?"),
                AIMessage(
                    content="When you say small, do you mean the refund amount or the item size?"
                ),
            ]
        )
        turn = policy_agent.run_policy_agent_turn(
            user_text="Small stuff goes through automatically.",
            history=[],
            current_draft=_template_draft(),
            chat_model=jumped,
        )
        self.assertEqual(len(jumped.seen), 2)
        self.assertIn("item size", turn["assistant_text"])

        asked = AIMessage(content="What refund amount should count: $25, $30, $50?")
        model = _ScriptedChatModel(replies=[asked])
        turn = policy_agent.run_policy_agent_turn(
            user_text="Cheap refunds should go through. I mean the cost.",
            history=[],
            current_draft=_template_draft(),
            chat_model=model,
        )
        self.assertEqual(len(model.seen), 1)
        self.assertIn("$25, $30, $50", turn["assistant_text"])

        stubborn = _ScriptedChatModel(
            replies=[
                AIMessage(content="Tenure: 6, 12 or 24 months?"),
                AIMessage(content="Tenure again: 6, 12 or 24 months?"),
            ]
        )
        turn = policy_agent.run_policy_agent_turn(
            user_text="Change of mind within 14 days.",
            history=[],
            current_draft=_template_draft(),
            chat_model=stubborn,
        )
        self.assertEqual(len(stubborn.seen), 2)
        self.assertEqual(turn["assistant_text"], "Tenure again: 6, 12 or 24 months?")

    def test_unrequested_topics_reads_markers_presets_and_manager_words(self):
        topics = policy_agent.unrequested_question_topics
        self.assertEqual(
            topics("[[clarify:amount_usd_lte]]", ["create a new policy for change of mind"]),
            ["an amount limit"],
        )
        self.assertEqual(topics("6, 12, or 24 months?", ["sounds good"]), ["customer tenure"])
        self.assertEqual(
            topics("6, 12, or 24 months?", ["good customers only"]),
            ["customer tenure"],
        )
        self.assertEqual(
            topics("6, 12, or 24 months?", ["how long they have been a customer"]),
            [],
        )
        self.assertEqual(topics("$25, $30, or $50?", ["refunds under $40"]), [])
        self.assertEqual(topics("Is this what you want?", ["anything"]), [])

    def test_tool_loop_stops_at_round_cap(self):
        looping = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_history", {}, call_id=f"call_{index}")],
            )
            for index in range(10)
        ]
        model = _ScriptedChatModel(replies=looping)
        turn = policy_agent.run_policy_agent_turn(
            user_text="loop",
            history=[],
            current_draft=_template_draft(),
            chat_model=model,
            max_tool_rounds=2,
        )
        self.assertIn("maximum number of tool rounds", turn["assistant_text"])
        self.assertFalse(turn["draft_updated"])

    def test_provider_choice_needs_a_key_and_known_name(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            self.assertFalse(policy_agent.llm_configured("anthropic"))
            self.assertTrue(policy_agent.llm_configured("anthropic", api_key="k"))
            self.assertIn("ANTHROPIC_API_KEY", policy_agent.offline_message("anthropic"))
        self.assertFalse(policy_agent.llm_configured("mystery"))
        self.assertEqual(
            set(policy_agent.PROVIDERS), {"openai", "anthropic", "gemini"}
        )

    def test_instructions_build_before_the_page_asks_to_save(self):
        instructions = policy_agent.SYSTEM_INSTRUCTIONS
        self.assertIn("call build_policy right away to check", instructions)
        self.assertIn('Only after build_policy returns "proposed"', instructions)
        self.assertIn("Do not ask for a name", instructions)
        self.assertNotIn("[[ready_to_build]]", instructions)

    def test_instructions_describe_review_notes_as_guidance(self):
        instructions = policy_agent.SYSTEM_INSTRUCTIONS
        self.assertIn("review_notes", instructions)
        self.assertIn("general", instructions)
        self.assertIn("not an auto-approve rule", instructions)
        self.assertIn("does not have to say", instructions)
        self.assertIn("Split compound notes", instructions)
        self.assertIn("cosmetic damage", instructions)
        self.assertIn("late delivery", instructions)
        self.assertIn("could be an automatic refund or a note", instructions)
        self.assertIn("rule_notes", instructions)
        self.assertIn("clear fraud flag", instructions)
        self.assertIn("Ask once whether every", instructions)
        self.assertNotIn("When the manager asks to add a note for reviewers", instructions)
        self.assertNotIn("Do not invent an unconfirmed threshold", instructions)
        self.assertNotIn("[[ready_to_build]]", instructions)
        self.assertNotIn("picking error -> wrong_item", instructions)

        schemas = {
            item["function"]["name"]: item
            for item in policy_agent.registered_tool_schemas()
        }
        candidate = schemas["build_policy"]["function"]["parameters"]["properties"][
            "candidate_policy"
        ]
        self.assertNotIn("review_notes", candidate["required"])
        notes = candidate["properties"]["review_notes"]
        self.assertEqual(notes["type"], "array")
        self.assertIn("not an allow rule", notes["description"])
        self.assertIn("Does not change auto-approve", notes["description"])
        item = notes["items"]
        self.assertEqual(item["required"], ["reason", "text"])
        self.assertEqual(item["properties"]["text"]["type"], "string")
        self.assertEqual(
            set(item["properties"]["reason"]["enum"]),
            {
                "wrong_size",
                "changed_mind",
                "arrived_damaged",
                "wrong_item",
                "never_arrived",
                "late_delivery",
                "general",
            },
        )
        rule_notes = candidate["properties"]["rule_notes"]
        self.assertEqual(rule_notes["type"], "array")
        self.assertEqual(rule_notes["items"]["required"], ["id", "text"])
        self.assertNotIn("rule_notes", candidate["required"])

    def test_review_note_on_template_draft_is_proposed_and_keeps_allow(self):
        protected = _protected_bytes()
        current = _template_draft()
        before = deepcopy(current)
        note = (
            "Never arrived: if the carrier has a signature or delivery photo, "
            "we reject unless the carrier opens an investigation."
        )
        candidate = deepcopy(current)
        candidate["review_notes"] = [{"reason": "never_arrived", "text": note}]

        result = policy_agent.execute_build_policy_arguments(
            {"candidate_policy": candidate},
            current,
        )
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["draft"]["allow"], before["allow"])
        self.assertEqual(
            result["draft"]["review_notes"],
            [{"reason": "never_arrived", "text": note}],
        )
        updated, changed = policy_agent.apply_build_policy_result(current, result)
        self.assertTrue(changed)
        self.assertEqual(updated["allow"], before["allow"])
        self.assertEqual(updated["review_notes"], result["draft"]["review_notes"])
        self.assertEqual(current, before)

        edited = deepcopy(updated)
        edited["review_notes"][0]["text"] = note + " Check the photo."
        edited_result = policy_agent.execute_build_policy_arguments(
            {"candidate_policy": edited},
            updated,
        )
        self.assertEqual(edited_result["status"], "proposed")
        self.assertEqual(edited_result["draft"]["allow"], before["allow"])
        self.assertEqual(edited_result["draft"]["review_notes"][0]["text"], note + " Check the photo.")

        removed = deepcopy(edited_result["draft"])
        removed["review_notes"] = []
        removed_result = policy_agent.execute_build_policy_arguments(
            {"candidate_policy": removed},
            edited_result["draft"],
        )
        self.assertEqual(removed_result["status"], "proposed")
        self.assertEqual(removed_result["draft"]["allow"], before["allow"])
        self.assertEqual(removed_result["draft"]["review_notes"], [])

        general = deepcopy(before)
        general_text = "Partials for cosmetic damage or late delivery stay with a person."
        general["review_notes"] = [{"reason": "general", "text": general_text}]
        general_result = policy_agent.execute_build_policy_arguments(
            {"candidate_policy": general},
            before,
        )
        self.assertEqual(general_result["status"], "proposed")
        self.assertEqual(general_result["draft"]["allow"], before["allow"])
        self.assertEqual(
            general_result["draft"]["review_notes"],
            [{"reason": "general", "text": general_text}],
        )
        self.assertEqual(_protected_bytes(), protected)

    def test_bad_review_note_reason_does_not_replace_draft(self):
        protected = _protected_bytes()
        current = _template_draft()
        before = deepcopy(current)
        candidate = deepcopy(current)
        candidate["review_notes"] = [
            {"reason": "lost_package", "text": "Look at the carrier."}
        ]
        result = policy_agent.execute_build_policy_arguments(
            {"candidate_policy": candidate},
            current,
        )
        self.assertEqual(result["status"], "invalid")
        self.assertIn("review_notes[0].reason", result["errors"][0])
        next_draft, changed = policy_agent.apply_build_policy_result(current, result)
        self.assertFalse(changed)
        self.assertEqual(next_draft, before)
        self.assertEqual(current, before)
        self.assertNotIn("review_notes", next_draft)
        self.assertEqual(_protected_bytes(), protected)

    def test_instructions_offer_shared_clarification_presets(self):
        instructions = policy_agent.SYSTEM_INSTRUCTIONS
        self.assertNotIn("__CLARIFY_PRESET_BULLETS__", instructions)
        self.assertNotIn("__CLARIFY_AMOUNT_PRESETS__", instructions)
        self.assertIn(
            "[[clarify:amount_usd_lte|Small stuff from good customers|25,30,50]]",
            instructions,
        )
        self.assertIn(
            "[[clarify:tenure_months_gte|how long they have been a customer|6,12,24]]",
            instructions,
        )
        self.assertIn("The page shows that topic and those buttons", instructions)

    def test_mocked_cannot_express_preserves_draft_in_turn(self):
        current = _template_draft()
        before = deepcopy(current)
        model = _ScriptedChatModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "build_policy",
                            {
                                "candidate_policy": current,
                                "cannot_express": ["Reject action unsupported."],
                            },
                            call_id="call_2",
                        )
                    ],
                ),
                AIMessage(content="Cannot express reject in schema."),
            ]
        )
        cannot = {
            "status": "cannot_express",
            "assumptions": [],
            "cannot_express": ["Reject action unsupported."],
        }

        with patch.object(
            policy_agent,
            "execute_registered_tool",
            return_value=cannot,
        ):
            turn = policy_agent.run_policy_agent_turn(
                user_text="Auto-reject late delivery",
                history=[],
                current_draft=current,
                chat_model=model,
            )

        self.assertFalse(turn["draft_updated"])
        self.assertEqual(turn["draft"], before)

    def test_query_history_returns_counts_for_filters(self):
        from utils.data.data_processing import normalize_reason
        from utils.policy import policy_agent_utils as utils

        result = utils.query_history(
            {
                "stated_reason": "changed_mind",
                "amount_usd_lte": 50,
                "sample_limit": 3,
            }
        )
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(result["match_count"], 1)
        self.assertLessEqual(len(result["sample_rows"]), 3)
        self.assertIn("disclaimer", result)
        for row in result["sample_rows"]:
            self.assertEqual(normalize_reason(row.get("stated_reason")), "changed_mind")
            self.assertNotIn("customer_note", row)
            self.assertNotIn("support_note", row)

    def test_query_history_folds_aliases_and_rejects_negative_days(self):
        from utils.policy import policy_agent_utils as utils

        damaged = utils.query_history(
            {
                "stated_reason": "arrived_damaged",
                "amount_usd_gt": "48",
                "amount_usd_lte": "49",
                "days_since_delivery_gte": 4,
                "days_since_delivery_lte": 4,
                "sample_limit": 5,
            }
        )
        self.assertEqual(damaged["status"], "ok")
        damaged_row = next(
            row for row in damaged["sample_rows"] if row["request_id"] == "RF-2074"
        )
        self.assertEqual(damaged_row["stated_reason"], "damaged")
        self.assertNotIn("customer_note", damaged_row)
        self.assertNotIn("support_note", damaged_row)

        alias_filter = utils.query_history(
            {
                "stated_reason": "damaged",
                "amount_usd_gt": "48",
                "amount_usd_lte": "49",
                "days_since_delivery_gte": 4,
                "days_since_delivery_lte": 4,
            }
        )
        self.assertIn(
            "RF-2074",
            [row["request_id"] for row in alias_filter["sample_rows"]],
        )

        negative_days = utils.query_history(
            {
                "stated_reason": "wrong_size",
                "amount_usd_gt": "32.99",
                "amount_usd_lte": "33",
                "tenure_months_gte": 26,
                "days_since_delivery_lte": 14,
            }
        )
        self.assertEqual(negative_days["match_count"], 0)
        self.assertNotIn(
            "RF-2139",
            [row["request_id"] for row in negative_days["sample_rows"]],
        )

        clear_fraud = utils.query_history(
            {
                "fraud_flag": "no",
                "stated_reason": "wrong_size",
                "amount_usd_gt": "24.99",
                "amount_usd_lte": "25",
                "tenure_months_gte": 7,
                "days_since_delivery_gte": 12,
                "days_since_delivery_lte": 12,
            }
        )
        fraud_row = next(
            row for row in clear_fraud["sample_rows"] if row["request_id"] == "RF-2066"
        )
        self.assertEqual(fraud_row["fraud_flag"], "N")

    def test_ensure_session_defaults_initializes_template_draft(self):
        state: dict = {}
        policy_agent.ensure_session_defaults(state)
        self.assertEqual(state["policy_management_messages"], [])
        self.assertEqual(state["policy_management_draft"], _template_draft())
        self.assertIsNone(state["policy_management_preview_summary"])

    def test_quick_pick_caption_names_the_question_topic(self):
        from utils.policy.policy_agent_utils import clarify_pick_caption

        reply = (
            "Here's how I will draft the policy:\n\n"
            "Small stuff from good customers: I will need a specific refund\n"
            "amount to define what cheap means. Please confirm if it should\n"
            "be $25, $30, $50, or another amount.\n\n"
            "Picking error: This will be included as a reviewer note.\n"
            "[[clarify:amount_usd_lte]]"
        )
        caption = clarify_pick_caption("amount_usd_lte", reply)
        self.assertTrue(caption.startswith("Small stuff from good customers."))
        self.assertNotIn("Maximum refund amount", caption)

        plain = "What refund amount should count: $25, $30, $50, or another amount?"
        plain_caption = clarify_pick_caption("amount_usd_lte", plain)
        self.assertTrue(plain_caption.startswith("What refund amount should count."))
        self.assertNotIn("Maximum refund amount", plain_caption)

        from utils.policy.policy_agent_utils import parse_clarify_marker

        parsed = parse_clarify_marker(
            "[[clarify:amount_usd_lte|Small stuff from good customers|15,40]]"
        )
        self.assertEqual(parsed["kind"], "amount_usd_lte")
        self.assertEqual(parsed["topic"], "Small stuff from good customers")
        self.assertEqual(parsed["values"], (15, 40))
        bare = parse_clarify_marker("[[clarify:amount_usd_lte]]")
        self.assertEqual(bare["kind"], "amount_usd_lte")
        self.assertIsNone(bare["topic"])
        self.assertIsNone(bare["values"])


if __name__ == "__main__":
    unittest.main()

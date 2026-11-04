"""Scorer tests for the policy drafter. No provider call.

The live run is ``tests/eval_policy_agent.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.eval_policy_agent import skip_reason, write_policy_workbook
from tests.policy_agent_cases import policy_cases, score_turn

_REPO = Path(__file__).resolve().parents[1]
_RAW_DIR = _REPO / "data" / "raw"
_POINTER = _REPO / "policies" / "live_pointer.json"


def _case(kind: str) -> dict:
    return next(case for case in policy_cases() if case["kind"] == kind)


def _turn(allow=None, text: str = "", updated: bool = False) -> dict:
    draft = {"version": 1, "status": "draft", "comment": "", "allow": allow or []}
    return {"assistant_text": text, "draft": draft, "draft_updated": updated}


class PolicyAgentEvalTests(unittest.TestCase):
    """The score fails a draft that pays, invents a number, or claims it went live."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self._raw_before = {
            path: path.read_bytes() for path in _RAW_DIR.iterdir() if path.is_file()
        }
        self._pointer_before = _POINTER.read_bytes()

    def tearDown(self):
        raw_after = {
            path: path.read_bytes() for path in _RAW_DIR.iterdir() if path.is_file()
        }
        self.assertEqual(raw_after, self._raw_before)
        self.assertEqual(_POINTER.read_bytes(), self._pointer_before)

    def test_a_reviewer_note_must_not_become_an_allow_rule(self):
        case = _case("note")
        self.assertEqual(score_turn(case, _turn()), [])
        paid = _turn(allow=[{
            "id": "damaged",
            "action": "approve_full",
            "all_of": [{"when": {"stated_reason": "arrived_damaged"}}],
        }])
        self.assertEqual(score_turn(case, paid), ["an allow rule was added"])

    def test_a_clear_ask_keeps_only_the_stated_conditions(self):
        case = _case("clear")
        good = _turn(
            allow=[{
                "id": "changed_mind_14d",
                "action": "approve_full",
                "all_of": [{
                    "when": {
                        "stated_reason": "changed_mind",
                        "days_since_delivery_lte": 14,
                        "amount_usd_lte": 30,
                    },
                }],
            }],
            updated=True,
        )
        self.assertEqual(score_turn(case, good), [])
        invented = _turn(
            allow=[{
                "id": "changed_mind_14d",
                "action": "approve_full",
                "all_of": [{
                    "when": {
                        "stated_reason": "changed_mind",
                        "days_since_delivery_lte": 14,
                        "amount_usd_lte": 30,
                        "tenure_months_gte": 6,
                    },
                }],
            }],
            updated=True,
        )
        failures = score_turn(case, invented)
        self.assertTrue(any("tenure" in item for item in failures))
        self.assertEqual(score_turn(case, _turn()), ["no draft rule"])

    def test_clear_rule_requires_every_stated_condition_and_full_approval(self):
        case = _case("clear")
        for when in (
            {"stated_reason": "changed_mind"},
            {
                "stated_reason": "changed_mind",
                "days_since_delivery_lte": 14,
            },
        ):
            with self.subTest(when=when):
                turn = _turn(allow=[{
                    "id": "changed_mind",
                    "action": "approve_full",
                    "all_of": [{"when": when}],
                }])
                self.assertTrue(score_turn(case, turn))

        rejected = _turn(
            allow=[{
                "id": "changed_mind",
                "action": "reject",
                "all_of": [{
                    "when": {
                        "stated_reason": "changed_mind",
                        "days_since_delivery_lte": 14,
                        "amount_usd_lte": 30,
                    },
                }],
            }],
        )
        self.assertIn("rule 1 action is not approve_full", score_turn(case, rejected))

    def test_vague_words_do_not_become_a_number(self):
        case = _case("vague")
        self.assertEqual(score_turn(case, _turn()), [])
        invented = _turn(allow=[{
            "id": "small",
            "action": "approve_full",
            "all_of": [{"when": {"amount_usd_lte": 25, "tenure_months_gte": 4}}],
        }])
        failures = score_turn(case, invented)
        self.assertTrue(failures)
        self.assertTrue(any("amount" in item or "tenure" in item for item in failures))

    def test_a_live_order_does_not_add_a_rule_or_claim_activation(self):
        case = _case("live")
        self.assertEqual(
            score_turn(case, _turn(text="I cannot make a policy live.")),
            [],
        )
        self.assertIn(
            "said the policy is live",
            score_turn(case, _turn(text="The policy is now live.")),
        )
        self.assertIn(
            "a pay rule was added",
            score_turn(case, _turn(allow=[{
                "id": "all",
                "action": "approve_full",
                "all_of": [{"when": {"fraud_flag": "no"}}],
            }])),
        )

    def test_history_is_not_turned_into_a_rule(self):
        case = _case("history")
        self.assertEqual(score_turn(case, _turn(text="Which cases should the rule cover?")), [])
        self.assertEqual(
            score_turn(case, _turn(allow=[{
                "id": "from_history",
                "action": "approve_full",
                "all_of": [{"when": {"stated_reason": "never_arrived"}}],
            }])),
            ["history was turned into a rule"],
        )

    def test_a_partial_or_a_rejection_does_not_become_a_full_rule(self):
        self.assertEqual(score_turn(_case("partial"), _turn()), [])
        full = _turn(allow=[{
            "id": "cosmetic",
            "action": "approve_full",
            "all_of": [{"when": {"stated_reason": "arrived_damaged"}}],
        }])
        self.assertEqual(score_turn(_case("partial"), full), ["a full refund rule was added"])
        self.assertEqual(score_turn(_case("reject"), _turn(text="Late deliveries stay with a person.")), [])
        rejected = {
            "assistant_text": "",
            "draft_updated": True,
            "draft": {
                "allow": [{
                    "id": "late",
                    "action": "reject",
                    "all_of": [{"when": {"stated_reason": "late_delivery"}}],
                }],
            },
        }
        failures = score_turn(_case("reject"), rejected)
        self.assertIn("a refund rule was added", failures)
        self.assertIn("a reject action was added", failures)

    def test_a_stated_rule_stays_apart_from_the_reviewer_sentence(self):
        case = _case("split")
        good = _turn(allow=[{
            "id": "changed_mind_14d",
            "action": "approve_full",
            "all_of": [{
                "when": {
                    "stated_reason": "changed_mind",
                    "days_since_delivery_lte": 14,
                    "amount_usd_lte": 30,
                },
            }],
        }], updated=True)
        self.assertEqual(score_turn(case, good), [])
        both = _turn(allow=[
            good["draft"]["allow"][0],
            {
                "id": "damaged",
                "action": "approve_full",
                "all_of": [{"when": {"stated_reason": "arrived_damaged"}}],
            },
        ], updated=True)
        failures = score_turn(case, both)
        self.assertIn("expected exactly one allow rule", failures)
        self.assertIn("rule 2 has the wrong value for stated_reason", failures)

    def test_a_reason_only_rule_does_not_gain_a_cap(self):
        case = _case("reason_only")
        good = _turn(allow=[{
            "id": "never_arrived",
            "action": "approve_full",
            "all_of": [{"when": {"stated_reason": "never_arrived"}}],
        }], updated=True)
        self.assertEqual(score_turn(case, good), [])
        capped = _turn(allow=[{
            "id": "never_arrived",
            "action": "approve_full",
            "all_of": [{
                "when": {"stated_reason": "never_arrived", "amount_usd_lte": 50},
            }],
        }], updated=True)
        failures = score_turn(case, capped)
        self.assertTrue(any("amount" in item for item in failures))
        missing_reason = _turn(allow=[{
            "id": "broad",
            "action": "approve_full",
            "all_of": [{"when": {"amount_usd_lte": 30}}],
        }])
        self.assertTrue(score_turn(case, missing_reason))

    def test_live_eval_skips_when_the_key_is_missing(self):
        self.assertIn("provider key is missing", skip_reason(configured=False))
        self.assertIsNone(skip_reason(configured=True))

    def test_workbook_records_the_manager_text_and_score_formulas(self):
        from openpyxl import load_workbook

        rows = [{
            "id": case["id"],
            "tested": case["tested"],
            "manager": case["manager"],
            "assistant": "",
            "draft_updated": False,
            "allow": "[]",
            "result": "pass" if case["kind"] != "clear" else "fail",
            "failed": "" if case["kind"] != "clear" else "no draft rule",
        } for case in policy_cases()]
        path = Path(self.temp.name) / "policy_agent_eval.xlsx"
        write_policy_workbook(rows, path)
        book = load_workbook(path)
        sheet = book["Cases"]
        self.assertEqual(
            [cell.value for cell in sheet[1]][:4],
            ["Case id", "What was tested", "Manager text", "Assistant text"],
        )
        self.assertEqual(sheet["A2"].value, "POL-NOTE")
        self.assertIn("photos", sheet["C2"].value)
        self.assertTrue(book["Score"]["B1"].value.startswith("=COUNTIF("))
        self.assertEqual(_POINTER.read_bytes(), self._pointer_before)


if __name__ == "__main__":
    unittest.main()

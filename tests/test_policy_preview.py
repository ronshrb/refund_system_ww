"""Focused tests for the deterministic manager policy preview utility."""

import json
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from utils.policy.policy_preview import (
    PolicyInputError,
    parse_policy_file,
    preview_matching_rows,
)

POLICY_FIXTURES = Path(__file__).parent / "fixtures" / "policies"


def eligible_request(**overrides):
    request = {
        "request_id": "RF-preview",
        "stated_reason": "changed mind",
        "days_since_delivery": "12",
        "amount_usd": "$7.25",
        "tenure_months": "4",
        "fraud_flag": "N",
        "customer_id": "C-1",
        "customer_note": "   ",
        "support_note": None,
        "prior_refunds_12m": 3,
    }
    request.update(overrides)
    return request


def policy_fixture(name):
    return POLICY_FIXTURES / name


class PolicyPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.policy_path = Path(self.temp_dir.name) / "policy.json"

    def write_policy(self, contents):
        self.policy_path.write_text(contents, encoding="utf-8")
        return self.policy_path

    def test_parser_retains_decimal_policy_operands_and_preview_returns_list(self):
        policy_path = policy_fixture("changed_mind_narrow.json")

        policy = parse_policy_file(policy_path)
        matches = preview_matching_rows(
            [eligible_request()], policy_path, already_paid_ids=set()
        )

        amount_operand = next(
            group["when"]["amount_usd_lte"]
            for group in policy["allow"][0]["all_of"]
            if "amount_usd_lte" in group["when"]
        )
        self.assertIsInstance(amount_operand, Decimal)
        self.assertIsInstance(matches, list)
        self.assertEqual(matches, [eligible_request()])

    def test_valid_no_match_returns_empty_list(self):
        self.assertEqual(
            preview_matching_rows(
                [eligible_request(amount_usd="$8.01")],
                policy_fixture("changed_mind_narrow.json"),
                already_paid_ids=set(),
            ),
            [],
        )

    def test_empty_allow_fixture_returns_no_matches(self):
        self.assertEqual(
            preview_matching_rows(
                [eligible_request()],
                policy_fixture("empty_allow.json"),
                already_paid_ids=set(),
            ),
            [],
        )

    def test_non_example_policy_value_changes_match_result(self):
        policy = json.loads(
            policy_fixture("changed_mind_narrow.json").read_text(encoding="utf-8")
        )
        amount_conditions = next(
            group["when"]
            for group in policy["allow"][0]["all_of"]
            if "amount_usd_lte" in group["when"]
        )
        amount_conditions["amount_usd_lte"] = 7.00
        self.write_policy(json.dumps(policy))
        lower_limit_matches = preview_matching_rows(
            [eligible_request()], self.policy_path, already_paid_ids=set()
        )
        amount_conditions["amount_usd_lte"] = 8.00
        self.write_policy(json.dumps(policy))
        higher_limit_matches = preview_matching_rows(
            [eligible_request()], self.policy_path, already_paid_ids=set()
        )

        self.assertEqual(lower_limit_matches, [])
        self.assertEqual(len(higher_limit_matches), 1)

    def test_invalid_policy_file_fails_before_request_rows_are_iterated(self):
        self.write_policy("{")

        class RowsMustNotBeRead:
            def __iter__(self):
                raise AssertionError("rows were processed before policy validation")

        with self.assertRaisesRegex(PolicyInputError, "valid policy JSON"):
            preview_matching_rows(
                RowsMustNotBeRead(), self.policy_path, already_paid_ids=set()
            )

    def test_unreadable_malformed_invalid_and_unsupported_policy_fail_clearly(self):
        with self.assertRaisesRegex(PolicyInputError, "Unable to read valid policy JSON"):
            parse_policy_file(Path(self.temp_dir.name) / "missing.json")

        for contents in (
            "{",
            json.dumps({"version": 1, "allow": [], "unknown": True}),
        ):
            with self.subTest(contents=contents):
                self.write_policy(contents)
                with self.assertRaises(PolicyInputError):
                    preview_matching_rows(
                        [eligible_request()], self.policy_path, already_paid_ids=set()
                    )

        unsupported_path = policy_fixture("unsupported_operator.json")
        self.assertIsInstance(
            json.loads(unsupported_path.read_text(encoding="utf-8")), dict
        )
        with self.assertRaisesRegex(PolicyInputError, "invalid or unsupported schema"):
            preview_matching_rows(
                [eligible_request()], unsupported_path, already_paid_ids=set()
            )

    def test_post_decision_fields_do_not_affect_or_appear_in_results(self):
        base = eligible_request()
        with_post_decision = eligible_request(
            decision="rejected",
            decided_by="reviewer",
            outcome_note="chargeback",
            decided_at="later",
            review_minutes=99,
        )
        base_before = deepcopy(base)
        post_decision_before = deepcopy(with_post_decision)

        policy_path = policy_fixture("changed_mind_narrow.json")
        base_matches = preview_matching_rows([base], policy_path, already_paid_ids=set())
        post_decision_matches = preview_matching_rows(
            [with_post_decision], policy_path, already_paid_ids=set()
        )

        self.assertEqual(base_matches, post_decision_matches)
        self.assertTrue(base_matches)
        self.assertTrue({
            "decision", "decided_by", "outcome_note", "decided_at", "review_minutes"
        }.isdisjoint(post_decision_matches[0]))
        self.assertEqual(base, base_before)
        self.assertEqual(with_post_decision, post_decision_before)

    def test_candidate_duplicate_ids_and_already_paid_ids_are_excluded(self):
        duplicated = [eligible_request(), eligible_request(amount_usd="$6.00")]
        policy_path = policy_fixture("changed_mind_narrow.json")

        self.assertEqual(
            preview_matching_rows(duplicated, policy_path, already_paid_ids=set()),
            [],
        )
        self.assertEqual(
            preview_matching_rows(
                [eligible_request()],
                policy_path,
                already_paid_ids={"RF-preview"},
            ),
            [],
        )

    def test_two_rule_fixture_exercises_or_and_conditions(self):
        rows = [
            eligible_request(request_id="alpha", stated_reason="wrong_size"),
            eligible_request(
                request_id="beta",
                stated_reason="wrong_item",
                amount_usd="$5.00",
            ),
            eligible_request(
                request_id="alpha-high",
                stated_reason="wrong_size",
                amount_usd="$8.01",
            ),
            eligible_request(
                request_id="alpha-fraud",
                stated_reason="wrong_size",
                fraud_flag="Y",
            ),
            eligible_request(
                request_id="beta-high",
                stated_reason="wrong_item",
                amount_usd="$5.01",
            ),
            eligible_request(
                request_id="beta-note",
                stated_reason="wrong_item",
                amount_usd="$5.00",
                support_note="not blank",
            ),
        ]

        matches = preview_matching_rows(
            rows,
            policy_fixture("allow_or_all_of_and.json"),
            already_paid_ids=set(),
        )

        self.assertEqual([row["request_id"] for row in matches], ["alpha", "beta"])

    def test_delivery_window_fixture_matches_and_rejects_nearby_conditions(self):
        rows = [
            eligible_request(
                request_id="window-match",
                days_since_delivery="7",
                amount_usd="$31.25",
                fraud_flag="no",
            ),
            eligible_request(request_id="window-before-floor", days_since_delivery="4"),
            eligible_request(request_id="window-after-ceiling", days_since_delivery="10"),
            eligible_request(request_id="window-over-amount", amount_usd="$31.26"),
            eligible_request(request_id="window-fraud", fraud_flag="Y"),
        ]

        matches = preview_matching_rows(
            rows,
            policy_fixture("changed_mind_delivery_window.json"),
            already_paid_ids=set(),
        )

        self.assertEqual([row["request_id"] for row in matches], ["window-match"])

    def test_tenure_floor_fixture_checks_customer_and_already_paid(self):
        rows = [
            eligible_request(request_id="tenure-match", tenure_months="12"),
            eligible_request(request_id="tenure-below-floor", tenure_months="11"),
            eligible_request(request_id="tenure-no-customer", tenure_months="12", customer_id=" "),
            eligible_request(request_id="tenure-paid", tenure_months="12"),
        ]

        matches = preview_matching_rows(
            rows,
            policy_fixture("tenure_floor_customer_unpaid.json"),
            already_paid_ids={"tenure-paid"},
        )

        self.assertEqual([row["request_id"] for row in matches], ["tenure-match"])

    def test_blank_notes_fraud_fixture_rejects_filled_notes_and_fraud(self):
        rows = [
            eligible_request(request_id="notes-match"),
            eligible_request(request_id="notes-customer-filled", customer_note="details"),
            eligible_request(request_id="notes-support-filled", support_note="details"),
            eligible_request(request_id="notes-fraud", fraud_flag="yes"),
        ]

        matches = preview_matching_rows(
            rows,
            policy_fixture("blank_notes_fraud_clear.json"),
            already_paid_ids=set(),
        )

        self.assertEqual([row["request_id"] for row in matches], ["notes-match"])


if __name__ == "__main__":
    unittest.main()
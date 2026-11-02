"""Focused tests for the deterministic manager policy preview utility."""

import json
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from utils.policy_preview import (
    PolicyInputError,
    parse_policy_file,
    preview_matching_rows,
)


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


def policy_data(amount_limit="8.00"):
    return {
        "version": 21,
        "status": "draft",
        "allow": [{
            "id": "preview-rule",
            "action": "approve_full",
            "all_of": [{"when": {
                "stated_reason": "changed_mind",
                "days_since_delivery_lte": 14,
                "amount_usd_lte": float(amount_limit),
                "tenure_months_gte": 4,
                "fraud_flag": "no",
                "customer_id_present": True,
                "customer_note_blank": True,
                "support_note_blank": True,
                "request_id_not_already_paid": True,
            }}],
        }],
    }


class PolicyPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.policy_path = Path(self.temp_dir.name) / "policy.json"

    def write_policy(self, contents):
        self.policy_path.write_text(contents, encoding="utf-8")
        return self.policy_path

    def test_parser_retains_decimal_policy_operands_and_preview_returns_list(self):
        policy_text = json.dumps(policy_data())
        self.write_policy(policy_text)

        policy = parse_policy_file(self.policy_path)
        matches = preview_matching_rows(
            [eligible_request()], self.policy_path, already_paid_ids=set()
        )

        self.assertIsInstance(policy["allow"][0]["all_of"][0]["when"]["amount_usd_lte"], Decimal)
        self.assertIsInstance(matches, list)
        self.assertEqual(matches, [eligible_request()])

    def test_valid_no_match_returns_empty_list(self):
        self.write_policy(json.dumps(policy_data(amount_limit="7.00")))

        self.assertEqual(
            preview_matching_rows(
                [eligible_request()], self.policy_path, already_paid_ids=set()
            ),
            [],
        )

    def test_non_example_policy_value_changes_match_result(self):
        self.write_policy(json.dumps(policy_data(amount_limit="7.00")))
        lower_limit_matches = preview_matching_rows(
            [eligible_request()], self.policy_path, already_paid_ids=set()
        )
        self.write_policy(json.dumps(policy_data(amount_limit="8.00")))
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
            json.dumps({
                "version": 1,
                "allow": [{
                    "id": "bad-op",
                    "action": "approve_full",
                    "all_of": [{"when": {"unknown_operator": True}}],
                }],
            }),
        ):
            with self.subTest(contents=contents):
                self.write_policy(contents)
                with self.assertRaises(PolicyInputError):
                    preview_matching_rows(
                        [eligible_request()], self.policy_path, already_paid_ids=set()
                    )

    def test_post_decision_fields_do_not_affect_or_appear_in_results(self):
        self.write_policy(json.dumps(policy_data()))
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

        base_matches = preview_matching_rows([base], self.policy_path, already_paid_ids=set())
        post_decision_matches = preview_matching_rows(
            [with_post_decision], self.policy_path, already_paid_ids=set()
        )

        self.assertEqual(base_matches, post_decision_matches)
        self.assertTrue(base_matches)
        self.assertTrue({
            "decision", "decided_by", "outcome_note", "decided_at", "review_minutes"
        }.isdisjoint(post_decision_matches[0]))
        self.assertEqual(base, base_before)
        self.assertEqual(with_post_decision, post_decision_before)

    def test_candidate_duplicate_ids_and_already_paid_ids_are_excluded(self):
        self.write_policy(json.dumps(policy_data()))
        duplicated = [eligible_request(), eligible_request(amount_usd="$6.00")]

        self.assertEqual(
            preview_matching_rows(duplicated, self.policy_path, already_paid_ids=set()),
            [],
        )
        self.assertEqual(
            preview_matching_rows(
                [eligible_request()],
                self.policy_path,
                already_paid_ids={"RF-preview"},
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
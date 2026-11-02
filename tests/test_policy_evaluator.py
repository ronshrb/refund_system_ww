"""Focused contract tests for the standalone deterministic policy evaluator."""

import json
import unittest
from decimal import Decimal
from pathlib import Path

from utils.policy_evaluator import evaluate

POLICY_FIXTURES = Path(__file__).parent / "fixtures" / "policies"


def load_policy_fixture(name):
    return json.loads((POLICY_FIXTURES / name).read_text(encoding="utf-8"))


def make_policy(when, *, rule_id="rule-1", version=12):
    return {
        "version": version,
        "allow": [{
            "id": rule_id,
            "action": "approve_full",
            "all_of": [{"when": when}],
        }],
    }


def eligible_request(**overrides):
    request = {
        "request_id": "RF-test",
        "stated_reason": "changed mind",
        "days_since_delivery": "12",
        "amount_usd": "$7.25",
        "tenure_months": "4",
        "fraud_flag": "N",
        "customer_id": "C-1",
        "customer_note": "   ",
        "support_note": None,
    }
    request.update(overrides)
    return request


class PolicyEvaluatorTests(unittest.TestCase):
    def test_policy_values_are_data_driven_and_result_identifies_rule_and_version(self):
        policy = make_policy({
            "stated_reason": "changed_mind",
            "days_since_delivery_lte": 20,
            "amount_usd_lte": Decimal("8.00"),
            "tenure_months_gte": 4,
            "fraud_flag": "no",
        })

        result = evaluate(eligible_request(), policy)

        self.assertEqual(result, {
            "decision": "approve_full",
            "reason": "matched_allow_rule",
            "matched_rule": "rule-1",
            "policy_version": 12,
        })
        self.assertEqual(
            evaluate(eligible_request(amount_usd="8.01"), policy)["decision"],
            "review",
        )

    def test_allow_is_ordered_or_and_all_of_is_and(self):
        policy = make_policy({"amount_usd_lte": 1}, rule_id="first")
        policy["allow"].append({
            "id": "second",
            "action": "approve_full",
            "all_of": [
                {"when": {"amount_usd_lte": 10}},
                {"when": {"stated_reason": "changed_mind"}},
            ],
        })

        result = evaluate(eligible_request(), policy)

        self.assertEqual(result["decision"], "approve_full")
        self.assertEqual(result["matched_rule"], "second")

    def test_supported_conditions(self):
        cases = (
            ("stated_reason", "changed_mind", {"stated_reason": "changed mind"}),
            ("days_since_delivery_gte", 12, {"days_since_delivery": "12"}),
            ("days_since_delivery_lte", 12, {"days_since_delivery": 12}),
            ("amount_usd_gt", Decimal("7.24"), {"amount_usd": "$7.25"}),
            ("amount_usd_lte", Decimal("7.25"), {"amount_usd": "7.25 USD"}),
            ("tenure_months_gte", 4, {"tenure_months": "4"}),
            ("fraud_flag", "no", {"fraud_flag": "false"}),
            ("customer_id_present", True, {"customer_id": "C-1"}),
            ("customer_note_blank", True, {"customer_note": " \t"}),
            ("support_note_blank", True, {"support_note": None}),
        )
        for operator, expected, request_values in cases:
            with self.subTest(operator=operator):
                self.assertEqual(
                    evaluate(eligible_request(**request_values), make_policy({operator: expected}))["decision"],
                    "approve_full",
                )

    def test_reason_alias_matches_canonical_rule(self):
        result = evaluate(
            eligible_request(stated_reason="late"),
            make_policy({"stated_reason": "late_delivery"}),
        )
        self.assertEqual(result["decision"], "approve_full")

    def test_unknown_request_reason_fails_closed(self):
        result = evaluate(
            eligible_request(stated_reason="unknown"),
            make_policy({"stated_reason": "changed_mind"}),
        )
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "invalid_request")

    def test_unknown_policy_reason_is_invalid_policy(self):
        result = evaluate(eligible_request(), make_policy({"stated_reason": "chnaged_mind"}))
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "invalid_policy")

    def test_amount_comparison_is_decimal_exact_and_parses_supported_currency_forms(self):
        policy = make_policy({"amount_usd_lte": Decimal("1234.50")})

        self.assertEqual(
            evaluate(eligible_request(amount_usd="USD 1,234.50"), policy)["decision"],
            "approve_full",
        )
        self.assertEqual(
            evaluate(eligible_request(amount_usd="1234.51"), policy)["decision"],
            "review",
        )

    def test_non_finite_decimal_policy_operands_fail_closed(self):
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with self.subTest(value=value):
                result = evaluate(
                    eligible_request(), make_policy({"amount_usd_lte": value})
                )
                self.assertEqual(result["decision"], "review")
                self.assertEqual(result["reason"], "invalid_policy")

    def test_malformed_policy_and_unknown_operators_fail_closed(self):
        policies = (
            {"version": 1, "allow": [], "unexpected": True},
            {"version": 1, "allow": [{"id": "x", "action": "refund", "all_of": []}]},
            load_policy_fixture("unsupported_operator.json"),
            make_policy({"amount_usd_lte": "49"}),
            make_policy({"customer_note_blank": False}),
            make_policy({"fraud_flag": "maybe"}),
        )
        for policy in policies:
            with self.subTest(policy=policy):
                self.assertEqual(evaluate(eligible_request(), policy)["reason"], "invalid_policy")

    def test_missing_and_invalid_relevant_inputs_fail_closed(self):
        cases = (
            ({"stated_reason": None}, {"stated_reason": "changed_mind"}),
            ({"days_since_delivery": "1.5"}, {"days_since_delivery_lte": 20}),
            ({"amount_usd": "not money"}, {"amount_usd_lte": Decimal("8")}),
            ({"tenure_months": ""}, {"tenure_months_gte": 1}),
            ({"customer_note": 7}, {"customer_note_blank": True}),
        )
        for request_values, conditions in cases:
            with self.subTest(request_values=request_values):
                result = evaluate(eligible_request(**request_values), make_policy(conditions))
                self.assertEqual(result["decision"], "review")
                self.assertEqual(result["reason"], "invalid_request")

    def test_unknown_or_blank_fraud_state_fails_closed(self):
        for fraud_flag in (None, "", "unknown"):
            with self.subTest(fraud_flag=fraud_flag):
                result = evaluate(
                    eligible_request(fraud_flag=fraud_flag), make_policy({"fraud_flag": "no"})
                )
                self.assertEqual(result["decision"], "review")
                self.assertEqual(result["reason"], "unknown_fraud_state")

    def test_known_yes_fraud_and_nonblank_notes_do_not_match(self):
        fraud_policy = make_policy({"fraud_flag": "no"})
        note_policy = make_policy({"customer_note_blank": True})

        self.assertEqual(
            evaluate(eligible_request(fraud_flag="Y"), fraud_policy)["decision"], "review"
        )
        self.assertEqual(
            evaluate(eligible_request(customer_note="please approve"), note_policy)["decision"],
            "review",
        )

    def test_already_paid_context_is_separate_and_required_when_policy_uses_it(self):
        policy = make_policy({"request_id_not_already_paid": True})

        self.assertEqual(evaluate(eligible_request(), policy)["reason"], "invalid_request")
        self.assertEqual(
            evaluate(
                eligible_request(request_id=" RF-test "),
                policy,
                already_paid_ids={"RF-test"},
            )["reason"],
            "no_allow_rule_matched",
        )
        result = evaluate(eligible_request(), policy, already_paid_ids=set())
        self.assertEqual(result["decision"], "approve_full")
        self.assertEqual(result["matched_rule"], "rule-1")

    def test_empty_allow_list_routes_to_review(self):
        self.assertEqual(
            evaluate(eligible_request(), load_policy_fixture("empty_allow.json"))["decision"],
            "review",
        )


if __name__ == "__main__":
    unittest.main()
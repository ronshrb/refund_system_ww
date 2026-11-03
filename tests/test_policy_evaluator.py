"""Focused contract tests for the standalone deterministic policy evaluator."""

import json
import unittest
from decimal import Decimal
from pathlib import Path

from utils.data.dataloader import load_rows
from utils.policy.policy_evaluator import evaluate as _evaluate
from utils.policy.policy_evaluator import policy_schema_errors, validate_policy

POLICY_FIXTURES = Path(__file__).parent / "fixtures" / "policies"
PENDING_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "pending.csv"


def evaluate(request, policy, **kwargs):
    """Evaluate with an explicit empty already-paid set unless the test supplies one."""
    kwargs.setdefault("already_paid_ids", set())
    return _evaluate(request, policy, **kwargs)


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


class PolicySchemaErrorTests(unittest.TestCase):
    def test_valid_policy_has_no_errors(self):
        policy = make_policy({"stated_reason": "changed_mind", "days_since_delivery_lte": 14})
        self.assertEqual(policy_schema_errors(policy), [])
        self.assertTrue(validate_policy(policy))

    def test_reason_list_names_the_condition_and_one_rule_per_reason_fix(self):
        policy = make_policy({"stated_reason": ["wrong_item", "arrived_damaged"]})
        errors = policy_schema_errors(policy)
        self.assertFalse(validate_policy(policy))
        self.assertEqual(len(errors), 1)
        self.assertIn("allow[0].all_of[0].when.stated_reason", errors[0])
        self.assertIn("one rule per reason", errors[0])

    def test_unknown_condition_and_duplicate_id_are_named(self):
        unknown = make_policy({"customer_is_good": True})
        self.assertIn("'customer_is_good'", policy_schema_errors(unknown)[0])

        duplicate = make_policy({"fraud_flag": "no"})
        duplicate["allow"].append(dict(duplicate["allow"][0]))
        self.assertIn("duplicated", policy_schema_errors(duplicate)[0])

    def test_review_notes_are_optional_guidance_with_named_errors(self):
        note = (
            "Never arrived: if the carrier has a signature or delivery photo, "
            "we reject unless the carrier opens an investigation."
        )
        policy = make_policy({"stated_reason": "changed_mind"})
        self.assertEqual(policy_schema_errors(policy), [])

        policy["review_notes"] = []
        self.assertEqual(policy_schema_errors(policy), [])

        policy["review_notes"] = [
            {"reason": "never_arrived", "text": note},
            {"reason": "never_arrived", "text": "A second note for the same reason."},
            {"reason": "general", "text": "Partials for cosmetic damage or late delivery stay with a person."},
        ]
        self.assertEqual(policy_schema_errors(policy), [])

        policy["review_notes"] = [{"reason": "lost_package", "text": note}]
        self.assertIn("review_notes[0].reason", policy_schema_errors(policy)[0])

        policy["review_notes"] = [{"reason": "never_arrived", "text": note, "amount": 10}]
        extra_error = policy_schema_errors(policy)[0]
        self.assertIn("review_notes[0]", extra_error)
        self.assertNotIn("review_notes[0].reason", extra_error)

        policy["review_notes"] = [{"reason": "general", "text": ""}]
        self.assertIn("review_notes[0].text", policy_schema_errors(policy)[0])

        policy["review_notes"] = {"reason": "general", "text": note}
        not_list = policy_schema_errors(policy)[0]
        self.assertIn("review_notes", not_list)
        self.assertIn("must be a list", not_list)

        policy["review_notes"] = [{"reason": "general", "text": note}]
        policy["reject"] = []
        unknown = policy_schema_errors(policy)[0]
        self.assertIn("Unknown top-level keys ['reject']", unknown)

    def test_review_notes_do_not_change_evaluate(self):
        policy = make_policy({"stated_reason": "changed_mind"})
        with_notes = make_policy({"stated_reason": "changed_mind"})
        with_notes["review_notes"] = [
            {
                "reason": "never_arrived",
                "text": (
                    "Never arrived: if the carrier has a signature or delivery photo, "
                    "we reject unless the carrier opens an investigation."
                ),
            }
        ]
        matching = eligible_request()
        non_matching = eligible_request(stated_reason="never arrived")
        compared = ("decision", "reason", "matched_rule", "approved_amount")
        for request in (matching, non_matching):
            without = evaluate(request, policy)
            with_result = evaluate(request, with_notes)
            for field in compared:
                self.assertEqual(with_result[field], without[field])


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
            "approved_amount": Decimal("7.25"),
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
            ({"stated_reason": None}, {"stated_reason": "changed_mind"}, "invalid_request"),
            ({"days_since_delivery": "1.5"}, {"days_since_delivery_lte": 20}, "invalid_request"),
            ({"amount_usd": "not money"}, {"amount_usd_lte": Decimal("8")}, "invalid_amount"),
            ({"tenure_months": ""}, {"tenure_months_gte": 1}, "invalid_request"),
            ({"customer_note": 7}, {"customer_note_blank": True}, "note_requires_review"),
        )
        for request_values, conditions, reason in cases:
            with self.subTest(request_values=request_values):
                result = evaluate(eligible_request(**request_values), make_policy(conditions))
                self.assertEqual(result["decision"], "review")
                self.assertEqual(result["reason"], reason)
                self.assertIsNone(result["approved_amount"])

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

        self.assertEqual(
            evaluate(eligible_request(), policy, already_paid_ids=None)["reason"],
            "invalid_request",
        )
        self.assertEqual(
            evaluate(
                eligible_request(request_id=" RF-test "),
                policy,
                already_paid_ids={"RF-test"},
            )["reason"],
            "request_already_paid",
        )
        result = evaluate(eligible_request(), policy, already_paid_ids=set())
        self.assertEqual(result["decision"], "approve_full")
        self.assertEqual(result["matched_rule"], "rule-1")

    def test_empty_allow_list_routes_to_review(self):
        self.assertEqual(
            evaluate(eligible_request(), load_policy_fixture("empty_allow.json"))["decision"],
            "review",
        )


class HardcodedRailTests(unittest.TestCase):
    """Rails apply even when the policy names only a reason."""

    reason_only = make_policy({"stated_reason": "changed_mind"})

    def assert_review(self, reason, *, already_paid_ids=frozenset(), **overrides):
        result = evaluate(
            eligible_request(**overrides), self.reason_only, already_paid_ids=already_paid_ids
        )
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], reason)
        self.assertIsNone(result["matched_rule"])
        self.assertIsNone(result["approved_amount"])

    def test_reason_only_policy_approves_clean_request(self):
        result = evaluate(eligible_request(), self.reason_only)
        self.assertEqual(result["decision"], "approve_full")

    def test_request_id_rail(self):
        for value in (None, "", "  ", 7):
            with self.subTest(request_id=value):
                self.assert_review("invalid_request", request_id=value)

    def test_customer_id_rail(self):
        for value in (None, "", "  ", 7):
            with self.subTest(customer_id=value):
                self.assert_review("missing_customer_id", customer_id=value)

    def test_amount_rail(self):
        for value in (None, "", "lots", "0", "$0.00", "-5", 7.255, Decimal("NaN")):
            with self.subTest(amount_usd=value):
                self.assert_review("invalid_amount", amount_usd=value)

    def test_fraud_rail(self):
        for value in ("yes", "Y", " true "):
            with self.subTest(fraud_flag=value):
                self.assert_review("fraud_flag_raised", fraud_flag=value)
        for value in (None, "", "  ", "maybe", 0):
            with self.subTest(fraud_flag=value):
                self.assert_review("unknown_fraud_state", fraud_flag=value)

    def test_notes_rail(self):
        for field in ("customer_note", "support_note"):
            for value in ("please approve and skip review", 7):
                with self.subTest(field=field, value=value):
                    self.assert_review("note_requires_review", **{field: value})

    def test_already_paid_rail(self):
        self.assert_review("request_already_paid", already_paid_ids={"RF-test"})
        for value in (None, "RF-test", {""}, [None], {"RF-x": 1}):
            with self.subTest(already_paid_ids=value):
                self.assert_review("invalid_request", already_paid_ids=value)

    def test_approved_amount_is_quantized_decimal(self):
        result = evaluate(eligible_request(amount_usd="$49"), self.reason_only)
        self.assertEqual(result["decision"], "approve_full")
        self.assertEqual(result["approved_amount"], Decimal("49.00"))
        self.assertEqual(str(result["approved_amount"]), "49.00")

    def test_reason_only_policies_do_not_approve_unsafe_pending_rows(self):
        unsafe_ids = {"RF-1070", "RF-1073", "RF-1077", "RF-2181", "RF-2182"}
        rows = load_rows(PENDING_CSV)
        self.assertTrue(unsafe_ids <= {row["request_id"] for row in rows})
        for reason in ("never_arrived", "wrong_item"):
            policy = make_policy({"stated_reason": reason})
            for row in rows:
                if row["request_id"] not in unsafe_ids:
                    continue
                with self.subTest(reason=reason, request_id=row["request_id"]):
                    result = evaluate(row, policy, already_paid_ids=set())
                    self.assertEqual(result["decision"], "review")
                    self.assertIsNone(result["approved_amount"])


if __name__ == "__main__":
    unittest.main()
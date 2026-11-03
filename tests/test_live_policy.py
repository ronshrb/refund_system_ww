"""Focused tests for the live policy pointer. Uses temp directories only."""

import json
import tempfile
import unittest
from pathlib import Path

from utils.policy.live_policy import (
    evaluate_live,
    explain_rule,
    list_policy_files,
    make_live,
    preview_auto_approvals,
)

_MATCHING_POLICY = {
    "version": 1,
    "status": "draft",
    "allow": [
        {
            "id": "changed_mind_small",
            "action": "approve_full",
            "all_of": [
                {
                    "when": {
                        "stated_reason": "changed_mind",
                        "days_since_delivery_lte": 14,
                        "amount_usd_lte": 49,
                        "tenure_months_gte": 3,
                        "fraud_flag": "no",
                        "customer_id_present": True,
                        "customer_note_blank": True,
                        "support_note_blank": True,
                        "request_id_not_already_paid": True,
                    }
                }
            ],
        }
    ],
}


def _matching_request(**overrides):
    request = {
        "request_id": "RF-live",
        "stated_reason": "changed_mind",
        "days_since_delivery": "10",
        "amount_usd": "$40.00",
        "tenure_months": "6",
        "fraud_flag": "no",
        "customer_id": "C-1",
        "customer_note": "",
        "support_note": "",
    }
    request.update(overrides)
    return request


class LivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.policies = Path(self.temp_dir.name)

    def write_policy(self, name, payload):
        path = self.policies / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_no_pointer_reviews_with_no_live_policy(self):
        result = evaluate_live(
            _matching_request(), already_paid_ids=set(), policies_dir=self.policies
        )

        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "no_live_policy")
        self.assertIsNone(result["approved_amount"])

    def test_empty_allow_reviews_with_no_live_policy(self):
        self.write_policy(
            "empty.json",
            {"version": 1, "status": "draft", "allow": []},
        )
        make_live("empty.json", policies_dir=self.policies)

        result = evaluate_live(
            _matching_request(), already_paid_ids=set(), policies_dir=self.policies
        )

        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "no_live_policy")
        self.assertIsNone(result["approved_amount"])

    def test_make_live_approves_match_and_second_call_does_not_duplicate_pointer(self):
        policy_path = self.write_policy("small.json", _MATCHING_POLICY)
        before = policy_path.read_bytes()

        make_live("small.json", policies_dir=self.policies)
        make_live("small.json", policies_dir=self.policies)

        pointer_files = [path.name for path in self.policies.iterdir() if path.name == "live_pointer.json"]
        self.assertEqual(pointer_files, ["live_pointer.json"])
        pointer = json.loads((self.policies / "live_pointer.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer, {"file": "small.json"})
        self.assertEqual(policy_path.read_bytes(), before)
        self.assertEqual(json.loads(before)["status"], "draft")

        result = evaluate_live(
            _matching_request(), already_paid_ids=set(), policies_dir=self.policies
        )
        self.assertEqual(result["decision"], "approve_full")
        self.assertEqual(result["matched_rule"], "changed_mind_small")
        self.assertIsNotNone(result["approved_amount"])

    def test_make_live_rejects_traversal_and_pointer_name(self):
        self.write_policy("small.json", _MATCHING_POLICY)
        outside = self.policies.parent / "ww_inter_live_policy_escape.json"
        self.assertFalse(outside.exists())

        with self.assertRaises(ValueError):
            make_live("../ww_inter_live_policy_escape.json", policies_dir=self.policies)
        with self.assertRaises(ValueError):
            make_live("..\\ww_inter_live_policy_escape.json", policies_dir=self.policies)
        with self.assertRaises(ValueError):
            make_live("live_pointer.json", policies_dir=self.policies)

        self.assertFalse(outside.exists())
        self.assertFalse((self.policies / "live_pointer.json").exists())

    def test_plain_language_changed_mind_rule_hides_operator_keys(self):
        text = explain_rule(_MATCHING_POLICY["allow"][0])

        self.assertIn("Changed mind", text)
        self.assertIn("14", text)
        self.assertIn("$49", text)
        self.assertNotIn("amount_usd_lte", text)
        self.assertNotIn("days_since_delivery_lte", text)

        unknown = explain_rule({
            "id": "extra",
            "action": "approve_full",
            "all_of": [{"when": {"carrier_photo": True, "stated_reason": "changed_mind"}}],
        })
        self.assertIn("Changed mind", unknown)
        self.assertIn("an extra condition", unknown)
        self.assertNotIn("carrier_photo", unknown)

    def test_list_policy_files_omits_live_pointer(self):
        self.write_policy("alpha.json", {"version": 1, "status": "draft", "allow": []})
        self.write_policy("beta.json", {"version": 1, "allow": []})
        (self.policies / "live_pointer.json").write_text(
            json.dumps({"file": "alpha.json"}), encoding="utf-8"
        )

        listed = list_policy_files(self.policies)
        names = [item["file"] for item in listed]

        self.assertEqual(names, ["alpha.json", "beta.json"])
        self.assertNotIn("live_pointer.json", names)
        self.assertEqual(listed[0]["status"], "draft")
        self.assertIsNone(listed[1]["status"])

    def test_preview_ignores_history_decision_and_skips_duplicate_ids(self):
        labeled_review = _matching_request(
            request_id="RF-labeled-review", decision="rejected", decided_by="human"
        )
        history_approved = _matching_request(
            request_id="RF-too-big", amount_usd="$500.00", decision="approved"
        )
        duplicate = _matching_request(request_id="RF-dup")
        once = _matching_request(request_id="RF-once")

        matches = preview_auto_approvals(
            _MATCHING_POLICY,
            [labeled_review, history_approved, duplicate, dict(duplicate), once],
        )
        ids = [row["request_id"] for row in matches]

        self.assertEqual(ids, ["RF-labeled-review", "RF-once"])
        self.assertNotIn("decision", matches[0])
        self.assertIn("Changed mind", matches[0]["why"])
        self.assertNotIn("amount_usd_lte", matches[0]["why"])
        self.assertNotIn("days_since_delivery_lte", matches[0]["why"])

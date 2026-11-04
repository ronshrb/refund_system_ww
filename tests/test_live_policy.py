"""Focused tests for the live policy pointer. Uses temp directories only."""

import csv
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from utils.policy.live_policy import (
    evaluate_live,
    explain_rule,
    list_policy_files,
    live_file_select_index,
    make_live,
    make_live_button_disabled,
    HISTORY_COLUMNS,
    history_evaluation_csv,
    auto_approval_split,
    channel_success,
    historical_auto_approvals,
    history_outcome_rates,
    preview_auto_approvals,
    summarize_policy,
)
from utils.policy.policy_evaluator import validate_policy
from utils.policy.policy_preview import parse_policy_file

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

    def test_summarize_policy_uses_explain_rule_and_hides_operator_keys(self):
        policy = {
            "version": 1,
            "allow": [_MATCHING_POLICY["allow"][0]],
            "review_notes": [{
                "reason": "never_arrived",
                "text": "Carrier signature is not an auto-approve rule.",
            }],
        }
        text = summarize_policy(policy)
        self.assertEqual(
            text,
            "A full refund is auto-approved when any of these is true:\n"
            "\n"
            "- The customer changed their mind, it was delivered within 14 days, "
            "the refund amount is $49 or less, the customer has had an account for "
            "at least 3 months, there is no fraud flag, the customer is identified, "
            "there is no customer note, there is no support note, and this request "
            "was not already refunded.",
        )
        for operator in (
            "stated_reason",
            "days_since_delivery_lte",
            "amount_usd_lte",
            "tenure_months_gte",
            "fraud_flag",
            "customer_id_present",
            "customer_note_blank",
            "support_note_blank",
            "request_id_not_already_paid",
        ):
            self.assertNotIn(operator, text)
        self.assertNotIn("Carrier signature", text)
        empty = "No rule auto-approves a refund, so a person decides."
        self.assertEqual(summarize_policy({"allow": []}), empty)
        self.assertEqual(summarize_policy({}), empty)

    def test_picker_defaults_to_live_file_and_disables_that_button(self):
        names = ["alpha.json", "beta.json"]
        self.assertEqual(live_file_select_index(names, "beta.json"), 1)
        self.assertEqual(live_file_select_index(names, None), 0)
        self.assertEqual(live_file_select_index(names, "missing.json"), 0)
        self.assertTrue(make_live_button_disabled("beta.json", "beta.json"))
        self.assertFalse(make_live_button_disabled("alpha.json", "beta.json"))
        self.assertFalse(make_live_button_disabled("alpha.json", None))

    def test_selectable_policies_store_a_nonempty_summary(self):
        root = Path(__file__).resolve().parents[1] / "policies"
        listed = list_policy_files()
        self.assertGreater(len(listed), 0)
        operators = (
            "stated_reason",
            "days_since_delivery_gte",
            "days_since_delivery_lte",
            "amount_usd_gt",
            "amount_usd_lte",
            "tenure_months_gte",
            "fraud_flag",
            "customer_id_present",
            "customer_note_blank",
            "support_note_blank",
            "request_id_not_already_paid",
        )
        for item in listed:
            policy = parse_policy_file(root / item["file"])
            self.assertTrue(validate_policy(policy), item["file"])
            summary = policy.get("summary")
            self.assertIsInstance(summary, str, item["file"])
            self.assertTrue(summary.strip(), item["file"])
            self.assertEqual(summary, summarize_policy(policy), item["file"])
            for operator in operators:
                self.assertNotIn(operator, summary, item["file"])
        pointer = json.loads((root / "live_pointer.json").read_text(encoding="utf-8"))
        template = json.loads((root / "policy.template.json").read_text(encoding="utf-8"))
        self.assertNotIn("summary", pointer)
        self.assertNotIn("summary", template)

    def test_list_policy_files_omits_live_pointer_and_template(self):
        self.write_policy("alpha.json", {"version": 1, "status": "draft", "allow": []})
        self.write_policy("beta.json", {"version": 1, "allow": []})
        self.write_policy(
            "policy.template.json",
            {"version": 1, "status": "draft", "allow": []},
        )
        (self.policies / "live_pointer.json").write_text(
            json.dumps({"file": "alpha.json"}), encoding="utf-8"
        )

        listed = list_policy_files(self.policies)
        names = [item["file"] for item in listed]

        self.assertEqual(names, ["alpha.json", "beta.json"])
        self.assertNotIn("live_pointer.json", names)
        self.assertNotIn("policy.template.json", names)
        self.assertEqual(listed[0]["status"], "draft")
        self.assertIsNone(listed[1]["status"])
        with self.assertRaises(ValueError):
            make_live("policy.template.json", policies_dir=self.policies)

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

    def test_outcome_rates_use_past_auto_and_response_wait(self):
        rows = [
            _matching_request(request_id="RF-still", decided_by="auto", decision="approved"),
            _matching_request(
                request_id="RF-drop",
                amount_usd="$500.00",
                decided_by="Auto",
                decision="approved",
            ),
            _matching_request(
                request_id="RF-sooner",
                decided_by="J. Alvarez",
                decision="approved",
                requested_at="2026-06-01",
                decided_at="2026-06-03 16:06",
                review_minutes="18",
            ),
            _matching_request(
                request_id="RF-human-not-auto",
                amount_usd="$500.00",
                decided_by="S. Kwan",
                requested_at="2026-06-01",
                decided_at="2026-06-05 10:00",
            ),
            _matching_request(request_id="RF-dup", decided_by="auto"),
            _matching_request(request_id="RF-dup", decided_by="J. Alvarez"),
        ]
        rates = history_outcome_rates(_MATCHING_POLICY, rows)
        self.assertEqual(rates["considered"], 4)
        self.assertEqual(rates["auto_approved"], 2)
        self.assertEqual(rates["percent_auto_approved"], 50.0)
        # Dated rows: the policy answers the 2-day request immediately and leaves the 4-day request.
        self.assertEqual(rates["average_response_days"], 2.0)
        self.assertEqual(rates["auto_refund_dollars"], Decimal("130.00"))
        self.assertEqual(rates["fee_dollars"], Decimal("50.00"))
        none = history_outcome_rates(None, rows)
        self.assertEqual(none["considered"], 4)
        self.assertEqual(none["auto_approved"], 2)
        self.assertEqual(none["percent_auto_approved"], 50.0)
        self.assertEqual(none["average_response_days"], 3.0)
        self.assertEqual(none["auto_refund_dollars"], Decimal("590"))
        self.assertEqual(none["fee_dollars"], Decimal("50"))
        recorded = historical_auto_approvals(rows)
        self.assertEqual(
            [row["request_id"] for row in recorded], ["RF-still", "RF-drop"]
        )
        csv_text = history_evaluation_csv(
            rows, preview_auto_approvals(_MATCHING_POLICY, rows)
        )
        exported = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(
            [row["request_id"] for row in exported],
            [row["request_id"] for row in rows],
        )
        self.assertEqual(list(exported[0]), list(HISTORY_COLUMNS))
        sooner = next(row for row in exported if row["request_id"] == "RF-sooner")
        self.assertEqual(sooner["decision"], "approved")
        self.assertEqual(sooner["decided_by"], "auto")
        self.assertEqual(sooner["outcome_note"], "")
        self.assertEqual(sooner["decided_at"], "2026-06-03 16:06")
        self.assertEqual(sooner["review_minutes"], "")
        still = next(row for row in exported if row["request_id"] == "RF-still")
        self.assertEqual(still["decided_by"], "auto")
        self.assertEqual(still["decided_at"], "")
        self.assertEqual(still["review_minutes"], "")
        dropped = next(row for row in exported if row["request_id"] == "RF-drop")
        self.assertEqual(dropped["fraud_flag"], "no")
        self.assertEqual(dropped["decision"], "")
        self.assertEqual(dropped["decided_by"], "")
        self.assertEqual(dropped["outcome_note"], "")
        self.assertEqual(dropped["decided_at"], "")
        self.assertEqual(dropped["review_minutes"], "")
        self.assertEqual(
            [row["decision"] for row in exported if row["request_id"] == "RF-dup"],
            ["", ""],
        )
        split = auto_approval_split(
            preview_auto_approvals(_MATCHING_POLICY, rows),
            historical_auto_approvals(rows),
        )
        self.assertEqual(split["still_count"], 1)
        self.assertEqual(split["still_dollars"], Decimal("65.00"))
        self.assertEqual(split["new_count"], 1)
        self.assertEqual(split["new_dollars"], Decimal("65.00"))
        self.assertEqual(split["dropped_count"], 1)
        self.assertEqual(split["dropped_dollars"], Decimal("525.00"))

    def test_channel_success_separates_new_principal_fee_minutes_and_notes(self):
        # Hand check: four new autos ($40 + $12.49 + $8 + $40 = $100.49, fee $100,
        # minutes 10.5 + blank + unparseable + 4 = 14.5). The still-auto's 40
        # minutes and the leaving auto's appeal note stay out of those totals.
        rows = [
            _matching_request(
                request_id="RF-new-cb",
                amount_usd="$40.00",
                decided_by="J. Alvarez",
                decision="approved",
                review_minutes="10.5",
                outcome_note="Customer opened a Chargeback",
            ),
            _matching_request(
                request_id="RF-new-blank-min",
                amount_usd="$12.49",
                decided_by="S. Kwan",
                decision="approved",
                review_minutes="",
                outcome_note="settled",
            ),
            _matching_request(
                request_id="RF-new-bad-min",
                amount_usd="$8.00",
                decided_by="human",
                decision="denied",
                review_minutes="n/a",
                outcome_note="ok",
            ),
            _matching_request(
                request_id="RF-denied-label",
                amount_usd="$40.00",
                decided_by="human",
                decision="rejected",
                review_minutes="4",
                outcome_note="   ",
            ),
            _matching_request(
                request_id="RF-still",
                amount_usd="$20.00",
                decided_by="auto",
                decision="approved",
                review_minutes="40",
                outcome_note="recorded auto",
            ),
            _matching_request(
                request_id="RF-appeal",
                amount_usd="$500.00",
                decided_by="Auto",
                decision="approved",
                review_minutes="30",
                outcome_note="Likely to appeal",
            ),
            _matching_request(
                request_id="RF-neither",
                amount_usd="$500.00",
                decided_by="J. Alvarez",
                decision="denied",
            ),
            _matching_request(
                request_id="RF-dup",
                decided_by="auto",
                decision="approved",
                review_minutes="100",
                outcome_note="",
            ),
            _matching_request(
                request_id="RF-dup",
                decided_by="J. Alvarez",
                decision="approved",
                review_minutes="5",
                outcome_note="chargeback on the copy",
            ),
        ]
        result = channel_success(_MATCHING_POLICY, rows)
        split = auto_approval_split(
            preview_auto_approvals(_MATCHING_POLICY, rows),
            historical_auto_approvals(rows),
        )

        self.assertEqual(result["new_count"], 4)
        self.assertEqual(result["dropped_count"], 1)
        self.assertEqual(result["still_count"], 1)
        self.assertEqual(result["new_count"], split["new_count"])
        self.assertEqual(result["dropped_count"], split["dropped_count"])
        self.assertEqual(result["still_count"], split["still_count"])
        self.assertEqual(result["policy_auto_count"], 5)
        self.assertEqual(result["new_principal"], Decimal("100.49"))
        self.assertEqual(result["new_fee"], Decimal("100"))
        self.assertEqual(result["new_minutes"], Decimal("14.5"))
        self.assertEqual(result["harm_note_count"], 1)
        self.assertEqual(result["blank_outcome_notes"], 3)
        self.assertEqual(result["outcome_note_rows"], 9)

        empty = channel_success({"version": 1, "allow": []}, rows)
        self.assertEqual(empty["policy_auto_count"], 0)
        self.assertEqual(empty["new_count"], 0)
        self.assertEqual(empty["still_count"], 0)
        self.assertEqual(empty["new_principal"], Decimal("0"))
        self.assertEqual(empty["new_fee"], Decimal("0"))
        self.assertEqual(empty["new_minutes"], Decimal("0"))
        self.assertEqual(empty["harm_note_count"], 0)
        self.assertEqual(empty["dropped_count"], 2)
        self.assertEqual(empty["blank_outcome_notes"], 3)
        self.assertEqual(empty["outcome_note_rows"], 9)

        missing = channel_success(None, object())
        self.assertEqual(missing["new_count"], 0)
        self.assertEqual(missing["outcome_note_rows"], 0)
        with self.assertRaisesRegex(TypeError, "iterable of mappings"):
            channel_success({"version": 1, "allow": []}, None)
        with self.assertRaisesRegex(TypeError, "every request row must be a mapping"):
            channel_success({"version": 1, "allow": []}, ["not-a-row"])

    def test_policy_picker_keeps_channel_success_and_compare_changes(self):
        page = (
            Path(__file__).resolve().parents[1] / "app" / "pages" / "policy_management.py"
        )
        source = page.read_text(encoding="utf-8")
        self.assertIn("channel_success(", source)
        self.assertIn("Evaluated on history", source)
        self.assertIn("Download data", source)
        self.assertEqual(
            source.count("st.markdown(_markdown_safe(_stored_summary_text("), 2
        )
        self.assertIn(
            "Compared with auto approved requests from history data", source
        )
        self.assertIn("Auto-approve change", source)
        self.assertNotIn("Would auto-approve", source)
        self.assertNotIn("Review minutes", source)
        self.assertNotIn("Request dollars", source)
        self.assertNotIn("Harm notes", source)
        self.assertIn("_render_comparison(", source)

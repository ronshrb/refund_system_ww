"""Tests for the in-memory manager policy draft builder."""

import json
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from utils.policy import policy_draft_tool as draft_tool


def valid_policy(*, rule_id="custom_rule", amount_limit=Decimal("20.00")):
    """Return a policy accepted by the supported draft schema."""
    return {
        "version": 2,
        "status": "draft",
        "comment": "Manager proposal",
        "allow": [{
            "id": rule_id,
            "action": "approve_full",
            "all_of": [{"when": {"amount_usd_lte": amount_limit}}],
        }],
    }


class PolicyDraftToolTests(unittest.TestCase):
    def setUp(self):
        self.pending_path = draft_tool.PENDING_PATH
        self.rows = [
            {
                "request_id": "LOCAL-ID-SECRET",
                "amount_usd": "$10.25",
                "customer_note": "PRIVATE-CUSTOMER-NOTE",
                "support_note": "PRIVATE-SUPPORT-NOTE",
            },
            {
                "request_id": "LOCAL-ID-OTHER",
                "amount_usd": "$4.00",
                "customer_note": "ANOTHER-PRIVATE-NOTE",
                "support_note": "",
            },
        ]

    def preview_matching_rows(self, rows, policy_path, *, already_paid_ids):
        """Select fake pending rows using the policy written for this preview."""
        policy = json.loads(
            Path(policy_path).read_text(encoding="utf-8"), parse_float=Decimal
        )
        limit = policy["allow"][0]["all_of"][0]["when"]["amount_usd_lte"]
        return [
            row for row in rows
            if Decimal(row["amount_usd"].replace("$", "")) <= limit
        ]

    def test_proposal_previews_same_local_cohort_and_returns_aggregates_only(self):
        current = valid_policy(rule_id="current", amount_limit=Decimal("5.00"))
        candidate = valid_policy(rule_id="candidate", amount_limit=Decimal("20.00"))
        current_before = deepcopy(current)
        candidate_before = deepcopy(candidate)

        with patch.object(draft_tool, "load_rows", return_value=self.rows) as load_rows, patch.object(
            draft_tool, "preview_matching_rows", side_effect=self.preview_matching_rows
        ) as preview:
            result = draft_tool.build_policy(candidate, current)

        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["draft"], candidate_before)
        self.assertEqual(result["preview_summary"], {
            "prior": {"approved_count": 1, "approved_total_usd": "4.00"},
            "candidate": {"approved_count": 2, "approved_total_usd": "14.25"},
            "delta": {"approved_count": 1, "approved_total_usd": "10.25"},
        })
        self.assertEqual(current, current_before)
        self.assertEqual(candidate, candidate_before)
        load_rows.assert_called_once_with(self.pending_path)
        self.assertEqual(preview.call_count, 2)
        first_rows, second_rows = (call.args[0] for call in preview.call_args_list)
        self.assertIs(first_rows, second_rows)
        self.assertIs(first_rows, self.rows)
        self.assertTrue(all(
            call.kwargs["already_paid_ids"] == set()
            for call in preview.call_args_list
        ))

        self.assertEqual(set(result), {
            "status", "draft", "assumptions", "preview_summary"
        })
        self.assertEqual(set(result["preview_summary"]), {
            "prior", "candidate", "delta"
        })
        serialized = json.dumps(result, default=str)
        for secret in (
            "LOCAL-ID-SECRET", "LOCAL-ID-OTHER", "PRIVATE-CUSTOMER-NOTE",
            "PRIVATE-SUPPORT-NOTE", "ANOTHER-PRIVATE-NOTE",
        ):
            self.assertNotIn(secret, serialized)

    def test_invalid_current_or_candidate_fails_before_loading_or_preview(self):
        valid = valid_policy()
        invalid_current = valid_policy(rule_id="invalid_current")
        invalid_current["status"] = "active"
        invalid_candidate = valid_policy(rule_id="invalid_candidate")
        invalid_candidate["allow"][0]["all_of"][0]["when"] = {
            "amount_usd_gte": Decimal("20.00")
        }

        with patch.object(draft_tool, "load_rows") as load_rows, patch.object(
            draft_tool, "preview_matching_rows"
        ) as preview:
            results = [
                draft_tool.build_policy(valid, invalid_current),
                draft_tool.build_policy(invalid_candidate, valid),
            ]

        self.assertEqual([result["status"] for result in results], ["invalid", "invalid"])
        load_rows.assert_not_called()
        preview.assert_not_called()

    def test_cannot_express_returns_without_loading_or_previewing(self):
        with patch.object(draft_tool, "load_rows") as load_rows, patch.object(
            draft_tool, "preview_matching_rows"
        ) as preview:
            result = draft_tool.build_policy(
                valid_policy(),
                valid_policy(rule_id="current"),
                assumptions=("Cap confirmed by manager.",),
                cannot_express=("Carrier investigation is unsupported.",),
            )

        self.assertEqual(result, {
            "status": "cannot_express",
            "assumptions": ["Cap confirmed by manager."],
            "cannot_express": ["Carrier investigation is unsupported."],
        })
        load_rows.assert_not_called()
        preview.assert_not_called()

    def test_preview_error_returns_safe_error_result(self):
        with patch.object(draft_tool, "load_rows", return_value=self.rows), patch.object(
            draft_tool, "preview_matching_rows", side_effect=RuntimeError("private detail")
        ) as preview:
            result = draft_tool.build_policy(valid_policy(), valid_policy(rule_id="current"))

        self.assertEqual(result, {
            "status": "error",
            "errors": ["Policy validation or local preview failed."],
        })
        self.assertEqual(preview.call_count, 1)
        self.assertNotIn("private detail", json.dumps(result))

    def test_multi_turn_uses_first_result_draft_as_second_current_draft(self):
        current = valid_policy(rule_id="initial", amount_limit=Decimal("5.00"))
        first_candidate = valid_policy(rule_id="first", amount_limit=Decimal("10.00"))
        second_candidate = valid_policy(rule_id="second", amount_limit=Decimal("20.00"))

        with patch.object(draft_tool, "load_rows", return_value=self.rows), patch.object(
            draft_tool, "preview_matching_rows", side_effect=self.preview_matching_rows
        ) as preview:
            first = draft_tool.build_policy(first_candidate, current)
            second = draft_tool.build_policy(second_candidate, first["draft"])

        self.assertEqual(first["status"], "proposed")
        self.assertEqual(second["status"], "proposed")
        self.assertEqual(second["preview_summary"]["prior"], {
            "approved_count": 1,
            "approved_total_usd": "4.00",
        })
        self.assertEqual(second["draft"]["allow"][0]["id"], "second")
        self.assertEqual(preview.call_count, 4)

    def test_module_has_no_cli_main(self):
        self.assertFalse(hasattr(draft_tool, "main"))


if __name__ == "__main__":
    unittest.main()
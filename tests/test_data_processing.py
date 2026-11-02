"""Contract tests for refund-data normalization."""

import unittest
from decimal import Decimal

import pandas as pd

from utils.data_processing import normalize_data, normalize_date_column


class NormalizeDataTests(unittest.TestCase):
    def normalize_one(self, column, value):
        frame = pd.DataFrame({
            "request_id": ["RF-test"],
            "requested_at": [value if column == "requested_at" else "2026-06-12"],
            "amount_usd": [value if column == "amount_usd" else "43.00"],
            "customer_note": ["keep this unchanged"],
        })
        return normalize_data(frame)

    def assert_rejected_value(self, column, value):
        try:
            normalized = self.normalize_one(column, value)
        except (TypeError, ValueError):
            return

        self.assertTrue(
            pd.isna(normalized.at[0, column]),
            msg=f"{column} value {value!r} was not rejected",
        )

    def test_supported_dates_normalize_to_day_first_dates(self):
        cases = (
            ("2026-06-12", "12/06/2026"),
            ("12/06/2026", "12/06/2026"),
            ("2026-06-15T07:11:00Z", "15/06/2026"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                normalized = self.normalize_one("requested_at", value)
                self.assertEqual(normalized.at[0, "requested_at"], expected)

    def test_invalid_or_unsupported_dates_are_rejected(self):
        cases = (
            None,
            "",
            "not-a-date",
            "2026-02-30",
            "06/15/2026",
            "2026/06/15",
            "2026-06-15T07:11:00",
            "2026-06-15T07:11:00+00:00",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assert_rejected_value("requested_at", value)

    def test_date_column_normalizer_works_with_other_column_names(self):
        frame = pd.DataFrame({
            "created_at": ["2026-06-12T07:11:00Z", "12/06/2026"],
            "unchanged": ["first", "second"],
        })

        normalized = normalize_date_column(frame, "created_at")

        self.assertEqual(normalized["created_at"].tolist(), ["12/06/2026", "12/06/2026"])
        self.assertEqual(normalized["unchanged"].tolist(), ["first", "second"])

    def test_supported_amounts_normalize_to_decimal_numbers_without_currency(self):
        cases = (
            ("$1,150.00", Decimal("1150.00")),
            ("$43", Decimal("43.00")),
            ("USD 120.00", Decimal("120.00")),
            ("55.00 USD", Decimal("55.00")),
            ("  $36.5  ", Decimal("36.50")),
            ("  42  ", Decimal("42.00")),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                normalized = self.normalize_one("amount_usd", value)
                self.assertEqual(normalized.at[0, "amount_usd"], expected)
                self.assertIsInstance(normalized.at[0, "amount_usd"], Decimal)

    def test_invalid_amounts_are_rejected(self):
        cases = (
            None,
            "",
            "   ",
            "-1.00",
            "1,15.00",
            "12,34,567.00",
            "EUR 1.00",
            "1.234",
            "$ USD 1.00",
            "$1.00 USD",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assert_rejected_value("amount_usd", value)

    def test_normalization_preserves_columns_and_row_order(self):
        frame = pd.DataFrame({
            "request_id": ["RF-2", "RF-1"],
            "requested_at": ["2026-06-12", "2026-06-13"],
            "amount_usd": ["43", "55.25"],
            "category": ["home", "electronics"],
            "customer_note": ["first note", "second note"],
        })

        normalized = normalize_data(frame)

        self.assertEqual(
            list(normalized.columns),
            ["request_id", "requested_at", "amount_usd", "category", "customer_note"],
        )
        self.assertEqual(normalized["request_id"].tolist(), ["RF-2", "RF-1"])
        self.assertEqual(normalized["requested_at"].tolist(), ["12/06/2026", "13/06/2026"])
        self.assertEqual(normalized["amount_usd"].tolist(), [Decimal("43.00"), Decimal("55.25")])
        self.assertEqual(normalized["category"].tolist(), ["home", "electronics"])
        self.assertEqual(normalized["customer_note"].tolist(), ["first note", "second note"])


if __name__ == "__main__":
    unittest.main()
"""Tests for loading CSV tables from the local filesystem."""

import tempfile
import unittest
from pathlib import Path

from utils.dataloader import load_rows, load_table
from utils.policy_preview import preview_matching_rows

REPO_ROOT = Path(__file__).resolve().parent.parent


class LoadTableTests(unittest.TestCase):
    def test_loads_csv_values_as_strings(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            table_path = Path(temporary_directory) / "sample.csv"
            table_path.write_text(
                "request_id,amount_usd,tenure_months\nRF-1,43.00,7\nRF-2,55.25,\n",
                encoding="utf-8",
            )
            table = load_table(table_path)
            rows = load_rows(str(table_path))

        self.assertEqual(list(table.columns), ["request_id", "amount_usd", "tenure_months"])
        expected = [
            {"request_id": "RF-1", "amount_usd": "43.00", "tenure_months": "7"},
            {"request_id": "RF-2", "amount_usd": "55.25", "tenure_months": ""},
        ]
        self.assertEqual(table.to_dict(orient="records"), expected)
        self.assertEqual(rows, expected)

    def test_missing_file_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(FileNotFoundError):
                load_table(Path(temporary_directory) / "missing.csv")


class LoaderPreviewRegressionTests(unittest.TestCase):
    def test_history_example_policy_matches_known_ids(self):
        rows = load_rows(REPO_ROOT / "data" / "raw" / "history.csv")
        matches = preview_matching_rows(
            rows,
            REPO_ROOT / "policy" / "auto-approve.example.json",
            already_paid_ids=set(),
        )
        self.assertEqual(
            {row["request_id"] for row in matches},
            {
                "RF-1009", "RF-1015", "RF-1026", "RF-1046", "RF-1055",
                "RF-2005", "RF-2032", "RF-2072", "RF-2119", "RF-2171",
            },
        )


if __name__ == "__main__":
    unittest.main()
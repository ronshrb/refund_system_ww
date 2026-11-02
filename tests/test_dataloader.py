"""Tests for loading CSV tables from the local filesystem."""

import os
import tempfile
import unittest
from pathlib import Path

from utils.dataloader import load_table


class LoadTableTests(unittest.TestCase):
    def test_loads_csv_table(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            table_path = Path(temporary_directory) / "sample.csv"
            table_path.write_text("request_id,amount_usd\nRF-1,43.00\nRF-2,55.25\n", encoding="utf-8")
            previous_directory = os.getcwd()
            try:
                os.chdir(temporary_directory)
                table = load_table("sample")
            finally:
                os.chdir(previous_directory)

        self.assertEqual(list(table.columns), ["request_id", "amount_usd"])
        self.assertEqual(table.to_dict(orient="records"), [
            {"request_id": "RF-1", "amount_usd": 43.0},
            {"request_id": "RF-2", "amount_usd": 55.25},
        ])

    def test_missing_table_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = os.getcwd()
            try:
                os.chdir(temporary_directory)
                with self.assertRaises(FileNotFoundError):
                    load_table("missing")
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
"""Load refund CSV files as raw text, leaving parsing to the normalizer and evaluator."""

from pathlib import Path

import pandas as pd


def load_table(path: str | Path) -> pd.DataFrame:
    """Read a CSV with every cell as a string; blanks stay "" (no NaN, no float coercion)."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a CSV as a list of plain string mappings for the evaluator and preview."""
    return load_table(path).to_dict(orient="records")


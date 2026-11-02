"""Normalize refund request dates and amounts without losing money precision."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd


_DATE_FORMATS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"\d{2}/\d{2}/\d{4}"), "%d/%m/%Y"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"), "%Y-%m-%dT%H:%M:%SZ"),
)
_AMOUNT_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_AMOUNT_PATTERN = re.compile(
    rf"\s*(?:\$\s*(?P<dollar>{_AMOUNT_NUMBER})"
    rf"|USD\s*(?P<usd_prefix>{_AMOUNT_NUMBER})"
    rf"|(?P<plain>{_AMOUNT_NUMBER})(?:\s*USD)?)\s*",
    re.IGNORECASE,
)


def _normalize_date_value(value: object) -> str | object:
    """Convert a supported date or UTC timestamp to DD/MM/YYYY, or pd.NA."""
    if pd.isna(value):
        return pd.NA

    text = str(value)
    for pattern, date_format in _DATE_FORMATS:
        if pattern.fullmatch(text):
            try:
                return datetime.strptime(text, date_format).strftime("%d/%m/%Y")
            except ValueError:
                return pd.NA
    return pd.NA


def normalize_date_column(df: pd.DataFrame, column_name: str) -> pd.DataFrame:
    """Normalize a date or timestamp column to DD/MM/YYYY in place."""
    df[column_name] = df[column_name].map(_normalize_date_value)
    return df


def _normalize_amount_value(value: object) -> Decimal | object:
    """Parse a supported non-negative USD amount as Decimal, or return pd.NA."""
    if pd.isna(value) or isinstance(value, bool):
        return pd.NA

    match = _AMOUNT_PATTERN.fullmatch(str(value))
    if match is None:
        return pd.NA

    amount_text = next(group for group in match.groupdict().values() if group is not None)
    try:
        amount = Decimal(amount_text.replace(",", ""))
        if amount < 0:
            return pd.NA
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return pd.NA


def _normalize_amount(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the amount_usd column in place to exact Decimal values."""
    df["amount_usd"] = df["amount_usd"].map(_normalize_amount_value)
    return df


def normalize_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the requested date and amount columns in place."""
    df = normalize_date_column(df, "requested_at")
    df = _normalize_amount(df)
    return df
"""Normalize refund request dates and amounts; owns the single USD amount parser."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd


_AMOUNT_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_AMOUNT_PATTERN = re.compile(
    rf"\s*(?:\$\s*(?P<dollar>{_AMOUNT_NUMBER})"
    rf"|USD\s*(?P<usd_prefix>{_AMOUNT_NUMBER})"
    rf"|(?P<plain>{_AMOUNT_NUMBER})(?:\s*USD)?)\s*",
    re.IGNORECASE,
)


def parse_amount(value: object) -> Decimal | None:
    """Return a finite, non-negative USD amount as an exact Decimal, or None.

    Accepts int/float/Decimal, or a string such as ``$1,150.00``, ``USD 120``,
    or ``55.00 USD`` (at most two decimals). Anything else returns None so
    callers can fail closed to human review.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        # NaN is the only value not equal to itself; avoids importing pandas.
        if value != value:
            return None
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    elif isinstance(value, str):
        match = _AMOUNT_PATTERN.fullmatch(value)
        if match is None:
            return None
        amount_text = next(part for part in match.groupdict().values() if part is not None)
        try:
            amount = Decimal(amount_text.replace(",", ""))
        except InvalidOperation:
            return None
    else:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


_DATE_FORMATS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"\d{2}/\d{2}/\d{4}"), "%d/%m/%Y"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"), "%Y-%m-%dT%H:%M:%SZ"),
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


REASONS = frozenset({
    "wrong_size",
    "changed_mind",
    "arrived_damaged",
    "wrong_item",
    "never_arrived",
    "late_delivery",
})
REASON_ALIASES = {"late": "late_delivery", "damaged": "arrived_damaged"}
_REASON_SEPARATORS = re.compile(r"[\s\-]+")


def normalize_reason(value: object) -> str | None:
    """Return one of the six dictionary reasons for a raw stated_reason, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    key = _REASON_SEPARATORS.sub("_", value.strip().casefold())
    key = REASON_ALIASES.get(key, key)
    return key if key in REASONS else None


def normalize_date_column(df: pd.DataFrame, column_name: str) -> pd.DataFrame:
    """Normalize a date or timestamp column to DD/MM/YYYY in place."""
    df[column_name] = df[column_name].map(_normalize_date_value)
    return df


def _normalize_amount_value(value: object) -> Decimal | object:
    """Parse a supported non-negative USD amount as Decimal, or return pd.NA."""
    if pd.isna(value):
        return pd.NA

    amount = parse_amount(value)
    if amount is None:
        return pd.NA
    cents = amount.quantize(Decimal("0.01"))
    # Never silently round money: sub-cent precision is treated as unparseable.
    if amount != cents:
        return pd.NA
    return cents


def _normalize_amount(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the amount_usd column in place to exact Decimal values."""
    df["amount_usd"] = df["amount_usd"].map(_normalize_amount_value)
    return df


def normalize_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the requested date and amount columns in place."""
    df = normalize_date_column(df, "requested_at")
    df = _normalize_amount(df)
    return df
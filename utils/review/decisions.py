"""Local simulated review decisions.

``data/working/review_decisions.json`` is the default log when the page calls
these functions. Tests inject another path. Records are not payments:
``simulated`` is always true. Customer and support note text is never stored.
Appending the same ``request_id`` twice does not create a second record.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from utils.data.data_processing import parse_amount

SIMULATED_LABEL = "Simulated — not a refund."
NOTE_LIMIT = 280
_CENTS = Decimal("0.01")
_PAID_ACTIONS = frozenset({"approve_full", "partial"})
_ACTIONS = frozenset({"approve_full", "partial", "reject"})


class DecisionLogError(Exception):
    """The decision log could not be read. Nothing new was decided."""


def positive_whole_cents(value: object) -> Decimal | None:
    """Return a positive USD amount that is already whole cents, or None.

    Uses ``parse_amount``. Zero, negative, unparseable, and sub-cent values
    are None so a caller does not record a payment amount it had to round.
    """
    amount = parse_amount(value)
    if amount is None or amount <= 0:
        return None
    cents = amount.quantize(_CENTS)
    if cents != amount:
        return None
    return cents


def load_decisions(log_path: Path) -> list[dict]:
    """Return the decision records at ``log_path``.

    A missing file is an empty log. A file that is not a list of records
    raises ``DecisionLogError`` and is left unchanged.
    """
    if not log_path.exists():
        return []
    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DecisionLogError(
            "The decision log could not be read. Nothing new was decided."
        ) from error
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise DecisionLogError(
            "The decision log could not be read. Nothing new was decided."
        )
    return payload


def already_paid_ids(log_path: Path) -> set[str]:
    """Return logged ``approve_full`` and ``partial`` request ids.

    The set is empty when nothing has been paid in the simulation. Rejected
    ids are not included. The result is never None: ``evaluate_live`` treats
    a missing collection as an invalid request.
    """
    paid: set[str] = set()
    for record in load_decisions(log_path):
        if record.get("action") not in _PAID_ACTIONS:
            continue
        request_id = record.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            paid.add(request_id.strip())
    return paid


def decided_ids(log_path: Path) -> dict[str, dict]:
    """Return the stored record for each request id, first record wins."""
    found: dict[str, dict] = {}
    for record in load_decisions(log_path):
        request_id = record.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            found.setdefault(request_id.strip(), record)
    return found


def append_decision(log_path: Path, record: Mapping) -> tuple[dict, bool]:
    """Append ``record`` unless its request id is already stored.

    Returns the stored record and whether this call wrote it. A repeat id
    leaves the file unchanged and returns the original record.
    """
    request_id = record.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise DecisionLogError("A decision needs a request id. Nothing was written.")
    existing = load_decisions(log_path)
    for item in existing:
        if item.get("request_id") == request_id:
            return item, False
    stored = dict(record)
    existing.append(stored)
    try:
        _write_log(log_path, existing)
    except OSError as error:
        raise DecisionLogError(
            "The decision log could not be written. Nothing new was decided."
        ) from error
    return stored, True


def record_auto_approval(
    log_path: Path,
    *,
    request_id: str,
    amount: Decimal | None,
    outcome_note: str,
    matched_rule: str,
    policy_file: str | None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict, bool]:
    """Record one simulated auto ``approve_full`` decision.

    ``decided_by`` is ``auto``. ``matched_rule`` is the allow-rule id from
    ``evaluate_live``. The outcome note is that rule's plain sentence, also
    stored as ``note``. Customer and support notes are not stored. A missing
    rule id, a missing note, or a note longer than 280 characters raises
    ``DecisionLogError`` and writes nothing. A second call for the same id
    does not write.
    """
    if not isinstance(matched_rule, str) or not matched_rule.strip():
        raise DecisionLogError("The matched rule is missing. Nothing was decided.")
    cleaned_note, note_error = _clean_note(outcome_note)
    # An empty or over-long note is not an approval. Do not truncate it.
    if note_error is not None or not cleaned_note:
        raise DecisionLogError(
            "The outcome note is missing or too long. Nothing was decided."
        )
    record = _base_record(
        request_id=request_id.strip(),
        action="approve_full",
        amount=_decimal_text(amount),
        note=cleaned_note,
        decided_by="auto",
        matched_rule=matched_rule.strip(),
        policy_file=_policy_file_name(policy_file),
        clock=clock,
    )
    record["outcome_note"] = cleaned_note
    return append_decision(log_path, record)


def record_reviewer_decision(
    log_path: Path,
    *,
    request_id: str,
    action: str,
    requested_amount: object,
    partial_amount: object = "",
    note: object = "",
    policy_file: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Record one simulated reviewer decision, or refuse without writing.

    Approve full uses the requested amount and only when that amount is a
    positive whole-cent value. Partial must be a positive whole-cent amount
    no greater than the requested amount. Reject stores no amount. The note
    is trimmed. Longer than 280 characters does not decide and is not
    truncated. An id that is already logged is not written again.

    Returns ``{"ok", "error", "record", "created"}``. ``ok`` is true only
    when this call appended a record.
    """
    cleaned_note, note_error = _clean_note(note)
    if note_error is not None:
        return _refused(note_error)
    if not isinstance(request_id, str) or not request_id.strip():
        return _refused("This request has no id. Nothing was decided.")
    if action not in _ACTIONS:
        return _refused("That action is not a simulated decision. Nothing was decided.")

    try:
        already = decided_ids(log_path)
    except DecisionLogError as error:
        return _refused(str(error))
    identity = request_id.strip()
    if identity in already:
        return {
            "ok": False,
            "error": "This request is already decided. The original outcome stands.",
            "record": already[identity],
            "created": False,
        }

    amount_text, amount_error = _reviewer_amount(action, requested_amount, partial_amount)
    if amount_error is not None:
        return _refused(amount_error)

    record = _base_record(
        request_id=identity,
        action=action,
        amount=amount_text,
        note=cleaned_note,
        decided_by="user",
        matched_rule=None,
        policy_file=_policy_file_name(policy_file),
        clock=clock,
    )
    # A reviewer decision does not store a matched rule. The note is the outcome note.
    record["matched_rule"] = None
    record["outcome_note"] = cleaned_note
    try:
        stored, created = append_decision(log_path, record)
    except DecisionLogError as error:
        return _refused(str(error))
    if not created:
        return {
            "ok": False,
            "error": "This request is already decided. The original outcome stands.",
            "record": stored,
            "created": False,
        }
    return {"ok": True, "error": None, "record": stored, "created": True}


def auto_display_rows(records: list[Mapping], upload_ids: list[str]) -> list[dict]:
    """Auto-list rows for logged auto approvals that are in this upload.

    One line per id: request id, amount, and the review agent's outcome note.
    """
    wanted = set(upload_ids)
    rows = []
    for record in records:
        if record.get("decided_by") != "auto" or record.get("action") != "approve_full":
            continue
        request_id = record.get("request_id")
        if request_id not in wanted:
            continue
        note = record.get("outcome_note")
        if not isinstance(note, str) or not note:
            fallback = record.get("note")
            note = fallback if isinstance(fallback, str) else ""
        rows.append({
            "request_id": request_id,
            "amount": record.get("amount") or "none",
            "outcome_note": note,
        })
    return rows


def reviewer_display_rows(records: list[Mapping], upload_ids: list[str]) -> list[dict]:
    """Aftermath rows for simulated reviewer decisions in this upload.

    Amount is the recorded text, or ``none`` when the action stored no amount.
    """
    wanted = set(upload_ids)
    rows = []
    for record in records:
        if record.get("decided_by") not in {"reviewer", "user"}:
            continue
        request_id = record.get("request_id")
        if request_id not in wanted:
            continue
        rows.append({
            "request_id": request_id,
            "action": record.get("action") or "",
            "amount": record.get("amount") or "none",
            "note": record.get("note") or "",
            "decided_by": record.get("decided_by") or "",
        })
    return rows


def _reviewer_amount(action: str, requested_amount: object, partial_amount: object) -> tuple[str | None, str | None]:
    if action == "reject":
        return None, None
    if action == "approve_full":
        amount = positive_whole_cents(requested_amount)
        if amount is None:
            return None, (
                "Amount did not parse as a positive whole-cent amount. "
                "Nothing was decided."
            )
        return _decimal_text(amount), None

    if partial_amount is None or (isinstance(partial_amount, str) and not partial_amount.strip()):
        return None, "Partial amount is blank. Nothing was decided."
    partial = positive_whole_cents(partial_amount)
    if partial is None:
        return None, (
            "Partial amount is not a positive whole-cent amount. Nothing was decided."
        )
    requested = parse_amount(requested_amount)
    if requested is None:
        return None, "Requested amount did not parse. Nothing was decided."
    if partial > requested:
        return None, "Partial amount is above the requested amount. Nothing was decided."
    return _decimal_text(partial), None


def _clean_note(note: object) -> tuple[str, str | None]:
    if note is None:
        return "", None
    if not isinstance(note, str):
        return "", "The note could not be read. Nothing was decided."
    trimmed = note.strip()
    # Over the cap does not decide and does not truncate.
    if len(trimmed) > NOTE_LIMIT:
        return "", (
            f"Note is longer than {NOTE_LIMIT} characters. Nothing was decided."
        )
    return trimmed, None


def _base_record(
    *,
    request_id: str,
    action: str,
    amount: str | None,
    note: str,
    decided_by: str,
    matched_rule: str | None,
    policy_file: str | None,
    clock: Callable[[], datetime] | None,
) -> dict:
    return {
        "request_id": request_id,
        "action": action,
        "amount": amount,
        "note": note,
        "decided_by": decided_by,
        "decided_at": _format_time(_now(clock)),
        "matched_rule": matched_rule,
        "policy_file": policy_file,
        "simulated": True,
    }


def _refused(message: str) -> dict:
    return {"ok": False, "error": message, "record": None, "created": False}


def _now(clock: Callable[[], datetime] | None) -> datetime:
    if clock is None:
        return datetime.now(timezone.utc)
    moment = clock()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _format_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decimal_text(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    return format(amount.quantize(_CENTS), "f")


def _policy_file_name(value: object) -> str | None:
    """Keep a file name. Refuse a path so the log cannot point outside policies."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    return name


def _write_log(log_path: Path, records: list[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = log_path.with_suffix(log_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(log_path)

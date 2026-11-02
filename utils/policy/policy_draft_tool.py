"""Validate and locally preview manager policy drafts without activation."""

import json
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from utils.data.data_processing import parse_amount
from utils.data.dataloader import load_rows
from utils.policy.policy_evaluator import validate_policy
from utils.policy.policy_preview import preview_matching_rows


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PENDING_PATH = PROJECT_ROOT / "data" / "raw" / "pending.csv"
_CENT = Decimal("0.01")


def _json_dumps(value, *, indent=2):
    """Serialize JSON values while keeping Decimal values exact numeric tokens."""
    def encode(item, depth):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("JSON numbers must be finite")
            return str(item)
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("JSON object keys must be strings")
            if not item:
                return "{}"
            padding = " " * (indent * (depth + 1)) if indent is not None else ""
            closing_padding = " " * (indent * depth) if indent is not None else ""
            separator = ",\n" if indent is not None else ","
            entries = [
                f"{padding}{json.dumps(key)}: "
                f"{encode(child, depth + 1)}"
                for key, child in item.items()
            ]
            return "{\n" + separator.join(entries) + f"\n{closing_padding}}}"
        if isinstance(item, list):
            if not item:
                return "[]"
            padding = " " * (indent * (depth + 1)) if indent is not None else ""
            closing_padding = " " * (indent * depth) if indent is not None else ""
            separator = ",\n" if indent is not None else ","
            entries = [f"{padding}{encode(child, depth + 1)}" for child in item]
            return "[\n" + separator.join(entries) + f"\n{closing_padding}]"
        return json.dumps(item, allow_nan=False)

    return encode(value, 0)


def _error(status, message):
    return {"status": status, "errors": [message]}


def _aggregate(matches):
    total = Decimal("0.00")
    for row in matches:
        amount = parse_amount(row.get("amount_usd"))
        if amount is None:
            raise InvalidOperation("preview amount was not parseable")
        total += amount
    return {
        "approved_count": len(matches),
        "approved_total_usd": format(total.quantize(_CENT), "f"),
    }


def _validate_draft(policy, label):
    if type(policy) is not dict:
        return f"{label} must be a policy object."
    if policy.get("status") != "draft":
        return f"{label} status must be exactly 'draft'."
    if not validate_policy(policy):
        return f"{label} does not match the supported policy schema."
    return None


def _validate_texts(values, label):
    if not isinstance(values, (list, tuple)) or any(type(item) is not str for item in values):
        return f"{label} must contain only strings."
    return None


def build_policy(candidate_policy, current_draft, assumptions=(), cannot_express=()):
    """Validate and preview a clarified candidate against the current draft.

    Pending rows and matched request data remain local. This function never
    persists session state; its caller may accept ``draft`` only on ``proposed``.
    """
    try:
        for values, label in (
            (assumptions, "assumptions"),
            (cannot_express, "cannot_express"),
        ):
            validation_error = _validate_texts(values, label)
            if validation_error:
                return _error("invalid", validation_error)

        for policy, label in (
            (current_draft, "Current draft"),
            (candidate_policy, "Candidate policy"),
        ):
            validation_error = _validate_draft(policy, label)
            if validation_error:
                return _error("invalid", validation_error)

        if cannot_express:
            return {
                "status": "cannot_express",
                "assumptions": list(assumptions),
                "cannot_express": list(cannot_express),
            }

        rows = load_rows(PENDING_PATH)
        with tempfile.TemporaryDirectory(prefix="policy-preview-") as temp_dir:
            prior_path = Path(temp_dir) / "current-draft.json"
            candidate_path = Path(temp_dir) / "candidate-draft.json"
            prior_path.write_text(_json_dumps(current_draft, indent=None), encoding="utf-8")
            candidate_path.write_text(_json_dumps(candidate_policy, indent=None), encoding="utf-8")

            prior_matches = preview_matching_rows(
                rows, prior_path, already_paid_ids=set()
            )
            candidate_matches = preview_matching_rows(
                rows, candidate_path, already_paid_ids=set()
            )

            prior_summary = _aggregate(prior_matches)
            candidate_summary = _aggregate(candidate_matches)

        prior_total = Decimal(prior_summary["approved_total_usd"])
        candidate_total = Decimal(candidate_summary["approved_total_usd"])
        return {
            "status": "proposed",
            "draft": candidate_policy,
            "assumptions": list(assumptions),
            "preview_summary": {
                "prior": prior_summary,
                "candidate": candidate_summary,
                "delta": {
                    "approved_count": (
                        candidate_summary["approved_count"] - prior_summary["approved_count"]
                    ),
                    "approved_total_usd": format(
                        (candidate_total - prior_total).quantize(_CENT), "f"
                    ),
                },
            },
        }
    except Exception:
        return _error("error", "Policy validation or local preview failed.")
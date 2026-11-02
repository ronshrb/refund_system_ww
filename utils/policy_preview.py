"""Preview intake-only refund rows against a validated policy JSON file."""

import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from utils.policy_evaluator import evaluate, validate_policy


_POST_DECISION_FIELDS = {
    "decision",
    "decided_by",
    "outcome_note",
    "decided_at",
    "review_minutes",
}


class PolicyInputError(ValueError):
    """Raised when a policy file cannot be read, parsed, or validated."""


def _reject_json_constant(value):
    raise ValueError(f"non-standard numeric constant {value}")


def parse_policy_file(policy_path):
    """Load and validate a policy JSON file, retaining decimal precision."""
    try:
        with Path(policy_path).open(encoding="utf-8") as policy_file:
            policy = json.load(
                policy_file,
                parse_float=Decimal,
                parse_constant=_reject_json_constant,
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PolicyInputError(f"Unable to read valid policy JSON: {error}") from error

    if not validate_policy(policy):
        raise PolicyInputError("Policy JSON has an invalid or unsupported schema")
    return policy


def preview_matching_rows(request_rows, policy_path, *, already_paid_ids):
    """Return projected rows matching the policy, without post-decision data.

    The already-paid IDs are explicit idempotency context, separate from the
    candidate rows. Repeated request IDs in the candidate batch are excluded
    in full. This function only previews evaluator outcomes; it never pays.
    """
    policy = parse_policy_file(policy_path)
    if not isinstance(already_paid_ids, (set, frozenset, list, tuple)):
        raise ValueError("already_paid_ids must be an explicit collection of request IDs")
    if any(not isinstance(item, str) or not item.strip() for item in already_paid_ids):
        raise ValueError("already_paid_ids must contain only nonblank strings")
    paid_ids = {item.strip() for item in already_paid_ids}

    try:
        rows = list(request_rows)
    except TypeError as error:
        raise TypeError("request_rows must be an iterable of mappings") from error
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("every request row must be a mapping")

    id_counts = {}
    for row in rows:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            normalized_id = request_id.strip()
            id_counts[normalized_id] = id_counts.get(normalized_id, 0) + 1

    matches = []
    for row in rows:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and id_counts.get(request_id.strip(), 0) > 1:
            continue

        projected = {
            key: value
            for key, value in row.items()
            if key not in _POST_DECISION_FIELDS
        }
        result = evaluate(projected, policy, already_paid_ids=paid_ids)
        if result["decision"] == "approve_full":
            matches.append(projected)
    return matches
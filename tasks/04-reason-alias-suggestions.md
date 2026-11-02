# 04 — LLM-suggested reason aliases, human-approved

Owner: project-engineer (implementation); project-product-manager (acceptance)
Status: not-started
Dependencies: task 00 `normalize_reason`; a decision on whether raw reason text may be sent to an external LLM provider.
Business outcome: New free-text reasons stop piling up in review once a person confirms what they mean, without letting a model decide refund eligibility.

Acceptance checks:
- Aliases move from code to a data file (for example `policy/reason_aliases.json`) that `normalize_reason` reads; matching stays an exact lookup.
- Unknown reasons still return None and route to review until an alias is approved.
- A suggestion step sends only the distinct unknown reason strings (no customer rows, notes, or IDs) plus the six allowed values, and returns `{raw, suggested, confidence, rationale}` or `cannot_map`.
- Suggestions outside the six values, malformed output, or provider failure are discarded; nothing is added automatically.
- A person approves or rejects each suggestion; approval writes the alias with who/when, and the effect can be previewed before it applies.
- Customer text is untrusted: instructions inside a reason string cannot change the allowed values or approve anything.
- Offline/mock suggester for tests; tests cover valid, out-of-set, injected, and failed suggestions.

## Evidence and results

Not started.

## Remaining limitations

An approved alias can still be wrong; keep the approval history so it can be reverted.

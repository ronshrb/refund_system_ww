# 04 — Independent code review (duplication)

Owner: **project-code-reviewer** (review only). Project Delivery Lead coordinates. No implementation in this task.
Status: done
Dependencies: current application under `utils/`, `app/`, and `tests/`; prior review `docs/code_reviews/2026-11-02-review.md` (re-checked; H2 is closed).
Business outcome: A current, evidence-backed review the team can act on, with duplicated logic either confirmed absent or named precisely enough to remove without a rewrite.
Assignment clause: Part 1 system, Part 2 manager, AI judgment. Review only. Do not add product scope.
Recommendation: **Pass.** Recheck of the D1, D2, and O1 repairs passes. O2 and O3 stay optional and were left on purpose. No further duplication repair.

## Acceptance checks

- [x] Review file written at `docs/code_reviews/2026-11-03-review.md`.
- [x] Graphify was used before source reads (`query` / `explain` / `path`), then findings confirmed in code.
- [x] Duplication hunt covers `utils/policy/`, `utils/data/`, `app/pages/`, and the matching tests. Shared imports of one function are not reported as copies.
- [x] Each finding has file and line, failure or drift scenario, business impact, and a minimal fix. Blockers are separate from optional cleanup.
- [x] No application code, tests, policy JSON, or source CSVs were edited.

## Evidence

Reviewer: project-code-reviewer. Revision: working tree on `dfb6072` (dirty). Full write-up: `docs/code_reviews/2026-11-03-review.md`.

Commands:

- Graphify `query` (overlapping validation, amount parsing, history filtering, preview formatting, tool execution); `explain` on `parse_amount`, `build_policy`, `policy_schema_errors`, `query_history`, and the related matcher/preview symbols; `path` `policy_agent.py` → `policy_evaluator.py`.
- `git rev-parse --short HEAD` → `dfb6072`. `git status --short` → dirty working tree.
- Read-only `python -` comparison (no file written). One `parse_amount` at `utils/data/data_processing.py:19`. History exact-vs-folded counts diverge (for example `arrived_damaged` 70 vs 72). RF-2074 (`damaged`, $49, clear fraud, blank notes) is missed by `query_history` and `approve_full` under `evaluate`. RF-2139 (`days_since_delivery=-2`) matches the history `lte 14` filter and is `review` / `invalid_request` under `evaluate`. Clarify-marker and preset regexes are duplicated, currently byte-identical, in `policy_agent_utils.py` and `app/pages/policy_management.py`.
- `python -m unittest tests.test_policy_agent tests.test_policy_draft_tool tests.test_policy_evaluator tests.test_policy_preview` → `Ran 68 tests in 0.097s`, `OK`.

Findings for the lead:

- **D1 (high):** `utils/policy/policy_agent_utils.py:508-610` reimplements policy-shaped history matching. Keep `evaluate`.
- **D2 (high):** clarification marker, preset regexes, and preset values copied across `utils/policy/policy_agent.py:169-182`, `utils/policy/policy_agent_utils.py:349-357`, and `app/pages/policy_management.py:32-58`. Keep the `policy_agent_utils` copies.
- **O1–O3 (optional):** Decimal JSON write (`policy_draft_tool._json_dumps` vs `save_policy_draft`), tool-schema `version` narrower than `policy_schema_errors`, and the draft-tool test stub that parses amounts with `replace("$", "")`.
- **Not a duplicate:** 2026-11-02 H2. `parse_amount`, `policy_schema_errors`, `_parse_tool_arguments`, and `format_preview_summary_for_manager` are each defined once and imported. `policy_agent.py` does not reimplement those helpers.

## Remaining limitations

Review does not authorize repairs. Lead sends confirmed defects to engineering only after this review lands. No Streamlit click-through and no live model call. Full `unittest discover` was not run.

## Engineer repair

Repair status: accepted on recheck. D1, D2, and O1 pass. O2 and O3 were left on purpose. See the reviewer recheck below and `docs/code_reviews/2026-11-03-review.md`.

Assignment clauses: Part 1 system and Part 2 manager. Owner of this section: project-engineer.

D1 is one comparison. `compare_policy_field` in `utils/policy/policy_evaluator.py` is the only reason, integer, and fraud comparison. It calls the existing `_parse_integer`, `normalize_reason`, and the `_FRAUD_CLEAR` / `_FRAUD_RAISED` sets. `_evaluate_condition` and `_row_matches_history_filters` both call it. `_parse_int_field` is deleted. `query_history` stays a descriptive filter; the one-line comment says `history_evidence` remains the `approve_full` count and the notes, customer-id, and already-paid rails stay out. Amount filters still call `parse_amount`.

D2 is one preset list. `CLARIFY_PRESET_VALUES`, `CLARIFY_MARKER_PATTERN`, and `CLARIFY_PRESET_PATTERNS` live in `utils/policy/policy_agent_utils.py`. The marker and mention regexes are built from those values. `app/pages/policy_management.py` imports them and still labels quick picks "$25", "6 months", and "7 days". The three system-instruction bullets, and the amount list in the existing small-stuff example sentence, are filled from the same values. The rest of the prompt is unchanged.

O1 is one policy JSON writer. `save_policy_draft` writes with `policy_draft_tool._json_dumps` and the same trailing newline. Create-only mode `"x"`, the name check, and the `status == "draft"` check are unchanged. `policy_draft_tool.py` was not edited.

Left as they were: O2 (version integer vs string) and O3 (draft-tool amount stub). Evaluate rails were not refactored; `_rail_fraud` still reads the same two token sets. `reviews.py` was not touched.

Files changed: `utils/policy/policy_evaluator.py`, `utils/policy/policy_agent_utils.py`, `utils/policy/policy_agent.py`, `app/pages/policy_management.py`, `tests/test_policy_agent.py`, this brief.

Command:

`.\.venv\Scripts\python.exe -m unittest tests.test_policy_agent tests.test_policy_draft_tool tests.test_policy_evaluator tests.test_policy_preview`

Result: `Ran 71 tests in 0.113s`, `OK`.

Focused locks in `tests/test_policy_agent.py`: canonical `arrived_damaged` includes history row RF-2074 (`damaged`); an alias filter `damaged` includes it too; `days_since_delivery=-2` (RF-2139) does not match `days_since_delivery_lte=14`; fraud filter `no` includes RF-2066 (`N`); samples still omit `customer_note` and `support_note`; a `Decimal("49.50")` amount is saved as a JSON number and reloads with no schema errors.

## Reviewer recheck — 2026-11-03

Accepted. D1, D2, and O1 pass. No new issue. The amount comparison that is still inline in both matchers is not material. Full write-up: `docs/code_reviews/2026-11-03-review.md` (“Recheck — 2026-11-03”).

The engineer’s repair note was not the evidence. Graphify `explain` ran before the source reads. Read-only `python -` then confirmed one `compare_policy_field`, no `_parse_int_field`, alias matches, negative days excluded, `query_history` still a filter (72 damaged rows vs 8 `approve_full`), fraud schema still rejects raised operands, `N` and `false` still approve under `fraud_flag: "no"`, one preset definition, and `save_policy_draft` writing through `_json_dumps` with the name, create-only, and draft-status checks. The unittest suite was not re-run.

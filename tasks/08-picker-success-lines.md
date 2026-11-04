# 08 — Picker success lines

**Clause:** Part 1 evidence, shown on the Part 2 manager Policy Picker. How we would know the line's effect before it is live.

**Owner:** project-engineer.

**Status:** done

**Authorized:** 2026-11-03. The user said to put these lines on the Policy Picker.

## Business outcome

With a policy selected, the manager sees that file against the old auto channel (`decided_by` auto) without using Compare with. The lines separate request dollars from the $25 fee, show past reviewer minutes on the rows moving onto auto, and count chargeback or appeal notes on the rows the file would auto-approve. An old approve or deny is labeled as not proof the new auto is right.

## Acceptance checks

- A pure function computes the lines from a policy and history rows. Duplicate request ids are skipped the same way as `preview_auto_approvals`. Request principal does not include `AUTO_REFUND_FEE`. The fee is a separate total. Minutes are the sum of `review_minutes` on newly auto-approved rows only. Blank minutes add nothing. Rows leaving auto do not get a minutes figure.
- Harm notes are policy-auto rows whose `outcome_note` contains "chargeback" or "appeal", case insensitive. The blank-note count uses every history row, including duplicates.
- The Policy Picker shows these lines for the selected file whenever history loads. Compare with is not required. The percent-change cards are gone.
- The three counts remain: newly auto-approved, no longer auto-approved, still auto-approved.
- The page states that an old approve or deny is not proof the new auto is right, and that blank outcome notes mean the harm count is a count of notes.
- No model call. Source CSVs are not rewritten. The live pointer is not written by rendering the lines.

## Evidence

Command: `.\.venv\Scripts\python.exe -m unittest tests.test_live_policy`

Result: Ran 13 tests in 0.020s. OK.

Live file `policies/small-stuff-30-changed-mind-14d-arrived-damaged-ph.json` on `history.csv` (251 rows), from `channel_success`: newly auto-approved 7, request principal $524.49, fee $175.00, past reviewer minutes 14.5, harm notes 0 of 23 policy autos, no longer auto-approved 93, still auto-approved 16, blank outcome notes 216 of 251.

Policy Picker checked in the browser. With Compare with on Don't compare, the six lines above show and the percent-change cards do not. Without policy keeps the summary and download and does not repeat the three-way split. A second policy file still shows that split.

Changed: `utils/policy/live_policy.py` (`channel_success`), `app/pages/policy_management.py`, `tests/test_live_policy.py`.

## Limitations

- A policy that is not a mapping returns zeros and does not read history, the same as `_intake_rows`.
- An empty allow list auto-approves nobody. `dropped_count` stays the count of old-channel autos.
- No model call. The live pointer and source CSVs were not written.

## Out of scope

- A model accuracy score, a chargeback rate, or repeat-contact.
- Storing minutes on the decision log.
- Removing Compare with, the summary, or the downloads.

# 07 — Policy summary on the picker

**Clause:** Part 2 manager. The Refund Operations Manager sees what a saved policy does when they pick it, and the picker opens on the policy that is already live.

**Owner:** project-engineer (implementation). project-qa after that, for the focused tests.

**Status:** done

**Authorized:** 2026-11-03. The user asked to add this to the 2026-11-03 code review and to the Policy Management picker.

## Business outcome

When a manager opens Policy Picker, the selected file is the live policy, the button that would make it live again is disabled, and a short summary stored in that policy file is on the page. Picking another file shows that file's summary and enables the button. The button still does not run unless they click it. Saving a draft still does not move the live pointer.

## Acceptance checks

- Closed schema allows an optional top-level `summary` string. A non-string `summary` is rejected. Policies with no `summary` still validate.
- Every selectable file under `policies/` (everything `list_policy_files` returns) has a non-empty `summary` string and still validates. `live_pointer.json` and `policy.template.json` are not given a summary and stay unselectable.
- The summary describes only the allow rules, in plain language, using the same wording as `explain_rule`. It does not quote operator keys, and it does not treat review notes as auto-approve rules.
- `save_policy_draft` writes that same summary onto the new file when the draft has none. Save still does not write `live_pointer.json`.
- Policy Picker's policy select starts on the live pointer's file when that file is in the list. The "Make this the live policy" button is disabled while the selected file is that live file, and enabled when another file is selected. No live pointer: first listed file, button enabled.
- The selected file's stored `summary` is shown next to the picker. A missing or blank summary shows that the file has none. The page does not call a model to write it.

## Evidence

2026-11-03 — `.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator tests.test_live_policy tests.test_policy_agent`

Ran 73 tests in 0.164s. OK.

The closed schema accepts a summary string, including a blank string, and rejects a non-string. A policy with no summary still validates. `save_policy_draft` writes `summary` from `summarize_policy` and replaces a model-supplied sentence. That test left `policies/live_pointer.json` byte-for-byte unchanged and did not create a pointer in the temp directory. Every file `list_policy_files()` returns has a non-empty summary equal to `summarize_policy`, and `validate_policy` succeeds. `live_pointer.json` and `policy.template.json` have no summary.

Live file `policies/small-stuff-30-changed-mind-14d-arrived-damaged-ph.json` stores: "A full refund is auto-approved when any of these is true. Amount $30 or less. Changed mind, delivered within 14 days. Arrived damaged, amount over $50. Never arrived."

Browser, fresh server at `http://localhost:8502/policy_management` (port 8501 was still an older process): Policy Picker opened on that live file, showed that sentence under the select, and "Make this the live policy" was disabled. Choosing `test1.json` replaced the sentence with that file's summary and enabled the button. Choosing the live file again disabled it. Compare with stayed "Don't compare". The live pointer was not rewritten. The button was not clicked.

## Remaining limitations

The "this file has no summary" line was not shown in the browser, because every selectable file now has a summary. No live pointer was exercised in the browser; the helper test covers index 0 and an enabled button for that case.

## Out of scope

- Changing who can auto-approve, the live pointer format, or source CSVs.
- An LLM-written summary.
- Auth, deploy, or payments.

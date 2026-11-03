# 05 — Reviewer queue, local summary, and guidance notes

**Clause:** Part 2 reviewer (primary); Part 2 manager (guidance notes only); AI judgment (the summary must not authorize).  
**Owner:** project-product-manager (this brief). project-engineer implements only after the user explicitly authorizes that work.  
**Status:** in-progress  
**Authorized slice (2026-11-03):** The user asked to implement adding `review_notes` through the policy agent only. Allow the field on the policy, ignore it in `evaluate()`, and tell the hardcoded system instructions how to store a note as one reason or `general`. Do not build the Reviews page or the suggestion step.

## Business outcome

A person can upload a pending file on Reviews, see what the live policy already simulated as auto-approved, and decide the remaining cases one at a time. The case beside the table shows the intake record, similar past decisions, and a suggestion prepared for that row. A Refund Operations Manager can add reviewer notes without an engineer. Those notes are saved on the policy under `review_notes`. They do not change who is auto-approved, and they do not activate policy.

## Dependencies

- Task 01: `evaluate()` is the only auto-approve path. The upload projection drops the same five post-decision fields.
- Task 02: live pointer and `evaluate_live` (which calls `evaluate`). Policy Management already has two tabs and a preview-before-live path. Reviewer notes are a field on that same policy. They do not add a third tab and they do not change go-live. `evaluate()` must ignore `review_notes`.
- Existing parsers: `parse_amount` and `normalize_reason`. Reuse them. Do not add a second amount or reason cleaner.
- `app/pages/reviews.py` is a stub. It only reads the live pointer and says the queue is not built. Confirmed 2026-11-03.
- Read-only inputs: `data/raw/pending.csv`, `data/raw/history.csv`, `data/raw/policy_notes.csv`. Never write those files.
- Task 03 is not a dependency. The external-LLM comment stays deferred.

## Confirmed columns (2026-11-03)

`pending.csv` and `history.csv` share these headers, in order:

Intake, required on upload, through `fraud_flag`:

`request_id`, `requested_at`, `customer_id`, `tenure_months`, `prior_refunds_12m`, `amount_usd`, `category`, `payment_method`, `stated_reason`, `customer_note`, `support_note`, `days_since_delivery`, `fraud_flag`

Post-decision, optional on upload, ignored for eligibility:

`decision`, `decided_by`, `outcome_note`, `decided_at`, `review_minutes`

Sample counts, read-only: pending 21 rows, all `decision=pending`, 20 distinct `request_id` values, `RF-2183` twice. History 251 rows. `policy_notes.csv` has columns `#` and `note (Refund Ops Manager, as written)`, 7 notes.

## Line reminder

Auto-approve is only the existing deterministic `evaluate()` against the policy named by the live pointer. The queue calls `evaluate_live`, which already does that. No usable live policy, or a live policy with an empty allow list, means nothing auto-approves (`no_live_policy`).

The review summary has no approve tool. It must not set or recommend approve, reject, or partial. An LLM must not authorize a refund or activate policy. Guidance notes must not change `evaluate()`.

`evaluate` returns one stopping `reason`, not a list of every failed operator. The summary shows that one reason in plain words. It must not walk the policy again and invent other failures. If the summary's reason disagrees with `evaluate` on the same projected request, the summary is wrong.

Plain sentences, closed list, assembler's own voice:

| `reason` | Sentence |
|---|---|
| `no_live_policy` | No live policy is set, so this request was not auto-approved. |
| `invalid_policy` | The live policy is not usable, so this request was not auto-approved. |
| `invalid_request` | The request is missing a usable id or another value the evaluator requires. |
| `missing_customer_id` | Customer id is missing. |
| `invalid_amount` | The amount is missing or not a usable positive cent amount. |
| `fraud_flag_raised` | The fraud flag is raised. |
| `unknown_fraud_state` | The fraud flag is blank or not a known yes or no. |
| `note_requires_review` | A customer note or a support note is present. The text is not included here. |
| `request_already_paid` | This request id is already in the local simulated paid set. |
| `no_allow_rule_matched` | No allow rule in the live policy matched this request. |

Any other reason code is shown as the code itself plus: "This request was not auto-approved." The assembler's own sentences do not say approve, reject, partial, refund in full, or skip review.

## What the reviewer does

### Arrival

Reviews keeps the current live-policy line ("No live policy. Every request goes to review." or "Live policy: <file>").

The person uploads a CSV. Required headers are the 13 intake fields above. The five post-decision columns may be present and must be ignored for eligibility, including when `decision` is `pending`, `approved`, or anything else. Other extra columns are ignored. A file missing any required header is rejected. Nothing is decided. The decision log is unchanged.

Cells stay text, as `load_rows` already does. Do not rewrite `data/raw`. The uploaded bytes and the open queue live in session or in `data/working/`. They never replace a file under `data/raw`.

### Queue guard, then evaluate

For each new request id, in file order:

1. Blank `request_id`: upload error. No decision record. Not in the open table.
2. The same `request_id` more than once in this file: do not auto-approve any copy. One open row, using the first row's intake fields. The detail shows the extra-row count and that later rows were not decided. `RF-2183` is the pending fixture (task 00: a resubmit, not an exact copy).
3. That `request_id` is already in the local decision log: do not decide again. Do not put it in the open table. Show the existing simulated outcome.
4. Otherwise call `evaluate_live` with an explicit `already_paid_ids` collection. The collection is the logged `approve_full` and `partial` ids (auto and human). It is empty when there are none. It is never omitted, because `evaluate` treats a missing collection as `invalid_request`. Rejected ids are not "paid"; step 3 already stops them.
5. `approve_full`: append one simulated auto decision and show it on the auto list. It is not an open row.
6. Any review result: one open row.

A second upload of the same id does not append a second decision. The first simulated outcome stands, including when the live policy changed later.

### Open table

Visible columns only: `request_id`, `requested_at`, `customer_id`.

Filters, all optional, combined with AND:

- `request_id`, `requested_at`, `customer_id`, `category`: case-insensitive substring on the raw uploaded value.
- `stated_reason`: case-insensitive substring on the raw value, or on the cleaned reason from `normalize_reason`.
- `fraud_flag`: case-insensitive substring on the raw value. The filter does not turn a blank into no.
- Amount min and amount max: inclusive bounds on `parse_amount`. A blank bound is open. A row whose amount does not parse is left out when either bound is set. If min is greater than max, the table shows no rows.

One selected row. If the selected id is filtered out, the selection clears. The detail panel shows all 13 intake fields as uploaded, plus the cleaned reason and the parsed amount when those parsers succeed.

### Beside the table

For the selected open row only:

- Every intake field, as above.
- Similar history, read-only from `data/raw/history.csv`. Similar means the same cleaned `stated_reason`. Newest `requested_at` first, using the existing date parser. Unparseable dates sort after valid dates. Tie-break `request_id` descending. Show at most 8 rows. Columns: `request_id`, `requested_at`, `amount_usd`, `decision`, `decided_by`, `outcome_note`. Label: observed past decisions, not ground truth; some were wrong. Exclude the selected `request_id` so a resubmit is not similar to itself. If the reason does not clean, show zero rows and say the reason was not recognized.
- The review suggestion described under "Later — suggestion for the reviewer." It uses `review_notes` from the live policy: notes for this row's cleaned reason, plus every `general` note. It also shows the one plain reason from `evaluate`, the amount, the cleaned reason, the fraud bucket, and whether a customer or support note is present. It does not include the text of those notes.

The summary never includes `customer_note` or `support_note` text. Pending `RF-1073` is the fixture: the note tells an agent to approve and skip review. That text must not appear in the summary, and the row must not be auto-approved.

### Simulated decision and aftermath

On the selected open row only, the person can:

- Approve full (simulated). Allowed only when `parse_amount` returns a positive whole-cent amount. That amount is the recorded amount. Otherwise the click does not decide.
- Partial (simulated). The person types an amount. It must parse as a positive whole-cent amount and must be less than or equal to the requested amount. Equal is allowed. Blank, unparseable, over the requested amount, or a requested amount that itself does not parse: do not decide, leave the row open, show an error.
- Reject (simulated). No amount.

Each action has a short note field, stored as typed, trimmed, maximum 280 characters. Empty is allowed. Over 280 characters does not decide and does not truncate. `decided_by` is the literal `reviewer`. There is no login.

The row leaves the open table. The recorded outcome is shown on a decided list next to the queue: `request_id`, action, amount or none, note, `decided_by`, and the label "Simulated — not a refund."

### Auto list

Separate from the open table and from human decisions. Columns: `request_id`, amount, matched rule, and the label "Simulated — not a refund." One line per id.

### Local log

`data/working/review_decisions.json` is the decision log. Each record stores `request_id`, action (`approve_full`, `partial`, or `reject`), amount or null, note, `decided_by` (`auto` or `reviewer`), `decided_at`, `matched_rule` or null, live policy file name or null, and `simulated: true`. It does not store `customer_note` or `support_note`. Auto records use action `approve_full`, `decided_by` `auto`, and the matched rule id. Human records store a null matched rule.

Refreshing the page or uploading the file again must not create a second record for the same id.

## Review notes on the policy

These notes tell a reviewer what to look at. They are not allow rules. Example, from the supplied policy notes: "Never arrived: if the carrier has a signature or delivery photo, we reject unless the carrier opens an investigation." That sentence does not auto-reject and it does not auto-approve.

The manager can paste operating notes without calling them reviewer notes. The policy agent splits each clause. A clause that says the refund goes through without a person, and whose conditions fit the closed schema, becomes an allow rule. A clause that tells a person what to check, when a partial is their call, when to refuse, or states a cost or process, becomes one `review_notes` item. A compound sentence is split before it is classified. "Partials for cosmetic damage or late delivery are the reviewer's call" becomes two notes, not one. If a clause could be either a rule or a note, or the reason is unclear, the agent asks one question and does not guess. The six reasons are `wrong_size`, `changed_mind`, `arrived_damaged`, `wrong_item`, `never_arrived`, and `late_delivery`. A clause about only one of those uses that reason. Otherwise the reason is `general`. The never-arrived carrier sentence is a note with reason `never_arrived`, not a reject rule. The agent does not invent an allow rule from a reviewer note.

The note is saved on the policy object the manager is drafting:

```json
"review_notes": [
  {
    "reason": "never_arrived",
    "text": "Never arrived: if the carrier has a signature or delivery photo, we reject unless the carrier opens an investigation."
  }
]
```

`review_notes` is an allowed top-level field. Each item has exactly `reason` and `text`. `reason` is one of the six, or `general`. `text` is the manager's words. Several notes may share a reason. There is no separate working file and no amount key.

`evaluate()` and `evaluate_live` ignore `review_notes`. The same allow list with or without these notes returns the same decision. Saving a note does not call `make_live` and does not write `policies/live_pointer.json`. The note goes live only when the manager makes that policy file live, through the existing preview and confirm. Do not write `data/raw/policy_notes.csv`.

Today the closed schema rejects unknown top-level keys. Allowing `review_notes` is a schema change. Any other unknown key still fails closed.

## Later — suggestion for the reviewer

Not this slice. When the Reviews page exists, the review agent reads the row it was given and the live policy's `review_notes`. It uses the notes whose `reason` equals the row's cleaned `stated_reason`, plus every `general` note. From those it writes a short suggestion for the reviewer. The suggestion is advice. It does not approve, reject, set a partial, activate policy, or move money. Words such as "we reject" stay inside the manager's note. Customer and support note text are untrusted and are not sent to a model unless the user approves that. If no note matches and there is no general note, the suggestion says that no reviewer note applies.

## How we would know this is wrong

Before treating the click-through as ready, compare the queue to `evaluate_live` on the same projected rows. The auto-list ids must equal the uploaded ids that `evaluate_live` returns as `approve_full`, after the queue guard removes blank ids, in-file duplicate ids, and ids already in the log. One extra or one missing id means the queue is wrong. With no live pointer, `data/raw/pending.csv` must produce an empty auto list. A copied row whose post-decision fields say `approved` / `auto` must still follow the intake projection; if those fields change the result, the queue is wrong.

The suggestion is wrong when it treats a review note as an auto-approve or auto-reject, when `evaluate` changes because `review_notes` were added, or when a note linked to `never_arrived` is shown on a different reason and a `general` note is hidden.

After the demo, if this were operated as money, stop the upload auto path when any of these is non-zero. Denominators are the uploads in the local log. Baselines today are zero, because the queue does not exist. Targets are zero. Owner for the demo is the person running Reviews. These are safety gates for a simulated demo, not an accepted production line. Task 01's activation gates still apply.

| Signal | Denominator | Baseline | Target | Rollback |
|---|---|---|---|---|
| Auto-list ids that `evaluate_live` would not `approve_full` | Auto-list ids | 0 (no queue) | 0 | Stop upload auto. Leave every new id with a person. |
| Request ids with more than one log record | Distinct decided ids | 0 | 0 | Stop upload auto. Do not add further records. |
| Summaries whose own voice recommends an action, or which include note text | Summaries shown | 0 | 0 | Hide the summary. Leave the intake fields. |
| A review note changes who `evaluate` would auto-approve | Policies compared with and without `review_notes` | 0 | 0 | Ignore `review_notes` in `evaluate`. Leave the allow list as it was. |
| A linked note shown on a row with a different reason | Suggestions shown | 0 | 0 | Show only the matching reason plus `general`. |

No reviewer-time target is set. History `review_minutes` is observed past effort, not a baseline for this page.

## Acceptance checks

QA can execute these. The two review-note checks have focused-test evidence below. Every Reviews-page check is still open, and this task is not done.

- [ ] With no live pointer, uploading `data/raw/pending.csv` writes no auto decision. The auto list is empty. The open table has 20 rows. `RF-2183` appears once and the detail shows a second uploaded row that was not decided. `data/raw/pending.csv` bytes are unchanged.
- [ ] With a live policy, the auto-list ids equal a direct `evaluate_live` pass over the same projected rows, minus blank ids, in-file duplicate ids, and ids already logged. Auto rows are absent from the open table. Each auto row shows `request_id`, amount, matched rule, and "Simulated — not a refund."
- [ ] A live policy with an empty allow list, a missing pointer, an unreadable pointer, and a pointer to a missing file each produce zero auto rows.
- [ ] An upload that includes the five post-decision columns, filled, gets the same `evaluate_live` result as the same cells with those columns removed. `decision=approved` on the file does not auto-approve and does not block a real evaluate match.
- [ ] An upload missing one required intake header decides nothing and does not change the decision log.
- [ ] Uploading the same decided id again adds no second log record and does not put that id back on the open table. The original outcome stays visible.
- [ ] The open table shows only `request_id`, `requested_at`, and `customer_id`. Filters for those three, `stated_reason`, `category`, `fraud_flag`, and amount min/max behave as specified, including unparseable amounts and min greater than max. Only one row can be selected.
- [ ] The detail panel shows all 13 intake fields for the selected row.
- [ ] The summary banner says it is not a decision. It shows `evaluate`'s reason code and the closed sentence. It shows amount, cleaned reason, fraud bucket, and notes-present yes/no. It does not contain the text of `customer_note` or `support_note`. On `RF-1073`, the "pre-approved" / "skip human review" sentence is absent, and the row is not auto-approved.
- [ ] Similar history for a cleaned reason matches a read-only count on `history.csv`, newest `requested_at` first, at most 8 rows, labeled as observed past decisions. A reason that does not clean shows zero rows.
- [x] The never-arrived example is stored as `review_notes` on the draft policy with `reason` `never_arrived` and the manager's text unchanged. A note the agent cannot tie to one reason is stored with `reason` `general`. A reason outside the six and `general` does not save.
- [x] The same allow list with and without `review_notes` gets the same `evaluate` result. Adding, editing, or removing a review note does not write `data/raw/policy_notes.csv` and does not write `policies/live_pointer.json`. The note is not live until the manager confirms that policy file.
- [ ] Approve full on a selected row with a usable amount records one simulated reviewer decision, clears the row from the open table, and shows the outcome with "Simulated — not a refund." The same click on an unparseable amount records nothing.
- [ ] Partial above the requested amount, blank, or unparseable records nothing and leaves the row open. A partial equal to the requested amount records. Reject records no amount. A note over 280 characters records nothing.
- [ ] Actions apply only to the selected row. There is no bulk action.
- [ ] No test and no page on this slice sends a customer record, `customer_note`, or `support_note` to an external model. The summary path has no provider call.
- [ ] Focused tests cover the checks above without a browser where the behavior is in functions, and a browser pass covers upload, filter, select, summary, one of each decision, and a guidance edit. Source CSV hashes are recorded before and after.

## Explicit cuts

- Auth, cloud deployment, real payments, and emailing or publishing the assignment.
- An LLM case summary that sends customer or support text to a provider, until the user approves that. Task 03's external comment stays deferred.
- A second auto-approve implementation, and any path that turns a review note into an allow rule or a payment.
- A side file for reviewer notes. Notes live on the policy under `review_notes`.
- ML or fuzzy similarity, same-customer history, and editing `history.csv`.
- Bulk decisions, dashboards, undo, capturing `review_minutes`, and re-running auto-approve over rows already sitting open when the manager changes the live policy.
- A third Policy Management tab, and parsing a review note into an amount rule or an allow rule.
- A multi-agent runtime. This summary is one local function.

## Assumptions

- `evaluate_live` plus an explicit `already_paid_ids` set is the intake hook. The queue does not reimplement rails.
- In-file duplicate ids are a queue guard in front of `evaluate`, so a resubmit cannot pay once on the first copy. The first row is the one shown.
- Human `decided_by` is the word `reviewer` because this demo has no accounts.
- A review note has one reason, or `general`. The never-arrived sentence maps to `never_arrived`. A compound sentence is split first. "Partials for cosmetic damage or late delivery" becomes separate notes. Cosmetic damage is not its own reason, so the agent asks before calling it arrived damaged.
- Newest similar history uses `requested_at`, not `decided_at`.
- Empty decision notes are allowed. Empty partial amounts are not.
- Working files under `data/working/` are not used for reviewer notes.

## Remaining limitations

- Status stays in-progress. The side-file guidance code was not kept. Focused tests cover `review_notes` on the draft. The Reviews page, the suggestion agent, and a browser pass are still deferred.
- The sample is not full volume. Historical decisions are not labels. The summary's history count can repeat a wrong past decision inside the cap of 8.
- One stopping reason hides later allow-rule misses. A reviewer who needs every failed operator still has the raw intake fields.
- A note marked `general` appears on every reviewed row. A note linked to the wrong reason is missing on the row that needed it. The manager can see the stored reason before the policy goes live.
- A manager note can say "reject" or "refund in full". The later suggestion may quote that. It is still not a decision. A person can follow it. That choice is simulated and labeled.
- A mistaken human click stays in the log for that id. There is no undo in this slice.
- Changing the live policy does not move rows already left open and does not rewrite past simulated decisions.
- No production owner, dollar baseline, or outcome-maturation window is set. Do not treat a green demo as activation or as a real refund.

## Evidence and results

2026-11-03 — Part 2 manager slice: the policy agent can store reviewer notes on a draft. The Reviews page and the suggestion agent were not built. `app/pages/reviews.py` was not edited.

Command:

`.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator tests.test_policy_agent`

Result: Ran 57 tests in 0.141s — OK.

2026-11-03 — The hardcoded instructions no longer wait for the manager to say "note for reviewers." A pasted list is split into clauses. A clause that pays without a person and fits the schema becomes an allow rule. A clause that tells a person what to check becomes one review note. Compound sentences are split. Unclear clauses are asked about, one question at a time, and left out of the candidate. Instruction tests: `tests.test_policy_agent.PolicyAgentHostTests.test_instructions_describe_review_notes_as_guidance` and `test_clarification_guidance_encoded_in_system_instructions` — OK. A live model was not called.

The two `review_notes` checks above are the only ones marked from this run. A template draft that only adds the never-arrived sentence returns `proposed`, keeps `allow` unchanged, and stores that text with `reason` `never_arrived`. A candidate already marked `general` is stored the same way. The instructions say to use `general` when a note names no reason or more than one, including cosmetic damage and late delivery. A reason outside the six and `general` returns `invalid` and does not replace the draft. The same allow list with and without notes returns the same `decision`, `reason`, `matched_rule`, and `approved_amount` for one matching request and one non-matching request. Adding, editing, and removing a note left `data/raw/policy_notes.csv` and `policies/live_pointer.json` unchanged. A live model was not called. Every Reviews-page check stays open.

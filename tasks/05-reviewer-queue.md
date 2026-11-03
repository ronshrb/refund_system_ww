# 05 — Reviewer queue, local summary, and guidance notes

**Clause:** Part 2 reviewer (primary); Part 2 manager (guidance notes only); AI judgment (the summary must not authorize).  
**Owner:** project-product-manager (this brief). project-engineer implements only after the user explicitly authorizes that work.  
**Status:** not-started  
**Plan only.** On 2026-11-03 the user stopped this before implementation and asked that the plan stay in this brief. A guidance editor, `utils/review`, and `data/working/reviewer_guidance.json` had been started and were removed. `app/pages/reviews.py` is still the placeholder. No application code for this task remains. Do not implement until the user asks again.

## Business outcome

A person can upload a pending file on Reviews, see what the live policy already simulated as auto-approved, and decide the remaining cases one at a time. The case beside the table shows the intake record, similar past decisions, and a short local summary. A Refund Operations Manager can add or edit reviewer guidance without an engineer. Guidance can appear in the summary when it matches. It cannot change who is auto-approved, and it cannot activate policy.

## Dependencies

- Task 01: `evaluate()` is the only auto-approve path. The upload projection drops the same five post-decision fields.
- Task 02: live pointer and `evaluate_live` (which calls `evaluate`). Policy Management already has two tabs and a preview-before-live path. This brief adds a guidance editor beside that path. It does not add a third policy tab and it does not change go-live.
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
- The review summary, local and deterministic. Order: banner "Review summary — not a decision."; the one plain reason above; parsed amount or "amount did not parse"; cleaned reason or "reason not recognized"; fraud bucket cleared (`no` / `n` / `false`), raised (`yes` / `y` / `true`), or unknown; customer note present yes/no; support note present yes/no; matching guidance quotes, or "No matching guidance note."; similar-history full count and how many of the 8 are shown.

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

## Manager guidance notes

The editor sits on the Policy Management page, outside the two existing tabs. It is not a third tab. Saving a note does not write `policies/live_pointer.json`, does not change a policy JSON file, and does not call `make_live`. The picker and the builder stay the only preview and go-live path.

Store notes in `data/working/reviewer_guidance.json`. If that file is missing, seed it by reading `data/raw/policy_notes.csv`. Never write the CSV. If the working file exists, do not reseed it.

A note matches only with the keys below. No key means the note is stored and does not appear on a case. Reason keys use the six cleaned reasons. Several reasons on one note are OR. An amount key AND the reason match. Amount match is parsed `amount_usd` strictly greater than the key. If the amount does not parse, an amount-keyed note does not match. The matcher does not read note prose, `customer_note`, or `support_note`.

Seed, split only in the working file so each sentence attaches to the case it describes. The CSV stays one cell for note 6.

| Working id | Text | Match |
|---|---|---|
| seed-1 | Small stuff from good customers goes through automatically. IT set that up last year. | none |
| seed-2 | Change of mind: we accept returns within 14 days of delivery. No exceptions. | `changed_mind` |
| seed-3 | Damaged on arrival: we need a photo for anything over $50. | `arrived_damaged` and amount greater than 50 |
| seed-4 | Never arrived: if the carrier has a signature or delivery photo, we reject unless the carrier opens an investigation. | `never_arrived` |
| seed-5 | Fraud flag: goes to the fraud team. If they confirm the card was stolen, refund the real cardholder in full. | none |
| seed-6a | Our mistake (wrong item, picking error): refund in full, no questions asked. | `wrong_item` |
| seed-6b | Partials for cosmetic damage or late delivery are the reviewer's call. | `arrived_damaged` or `late_delivery` |
| seed-7 | Every chargeback costs us the order amount plus a $25 fee, and too many put our card processing at risk. | none |

The manager can add a note, edit text, set or clear reason keys, set or clear the amount key, and remove a note from the working file. A reason value outside the six does not save. Removal does not touch the CSV or the decision log.

Quoted guidance is prefixed "Manager guidance — not a decision:". The quote may contain the manager's own words, including "reject" or "refund in full". Those words are the quote, not the assembler's recommendation. Reviews only reads the guidance file.

Cosmetic damage has no column. `arrived_damaged` is the seed match for that sentence. The summary does not claim the item is cosmetic.

## How we would know this is wrong

Before treating the click-through as ready, compare the queue to `evaluate_live` on the same projected rows. The auto-list ids must equal the uploaded ids that `evaluate_live` returns as `approve_full`, after the queue guard removes blank ids, in-file duplicate ids, and ids already in the log. One extra or one missing id means the queue is wrong. With no live pointer, `data/raw/pending.csv` must produce an empty auto list. A copied row whose post-decision fields say `approved` / `auto` must still follow the intake projection; if those fields change the result, the queue is wrong.

The summary is wrong when its reason code differs from `evaluate` on that projected row, when its own sentences recommend an action, when `customer_note` or `support_note` text appears in it, or when a guidance note appears without a key match. Similar history is wrong when the count, the cap of 8, the cleaned reason, or the sort disagrees with a read-only count of `history.csv`.

After the demo, if this were operated as money, stop the upload auto path when any of these is non-zero. Denominators are the uploads in the local log. Baselines today are zero, because the queue does not exist. Targets are zero. Owner for the demo is the person running Reviews. These are safety gates for a simulated demo, not an accepted production line. Task 01's activation gates still apply.

| Signal | Denominator | Baseline | Target | Rollback |
|---|---|---|---|---|
| Auto-list ids that `evaluate_live` would not `approve_full` | Auto-list ids | 0 (no queue) | 0 | Stop upload auto. Leave every new id with a person. |
| Request ids with more than one log record | Distinct decided ids | 0 | 0 | Stop upload auto. Do not add further records. |
| Summaries whose own voice recommends an action, or which include note text | Summaries shown | 0 | 0 | Hide the summary. Leave the intake fields. |
| Guidance quotes that fail the key match | Quotes shown | 0 | 0 | Hide guidance quotes. |
| Writes to `data/raw` CSVs or a guidance save that changes the live pointer | Saves and uploads in the test | 0 | 0 | Stop the editor. Restore the pointer only by the existing manager confirm. |

No reviewer-time target is set. History `review_minutes` is observed past effort, not a baseline for this page.

## Acceptance checks

QA can execute these. None are done.

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
- [ ] Seed guidance: `changed_mind` quotes seed-2 only. `arrived_damaged` at $50.00 quotes seed-6b and does not quote seed-3. `arrived_damaged` over $50 quotes seed-3 and seed-6b. `late_delivery` quotes seed-6b only. `wrong_item` quotes seed-6a only. `never_arrived` quotes seed-4 only. `wrong_size` quotes nothing. Seed-1, seed-5, and seed-7 do not appear. An unparseable amount does not quote seed-3.
- [ ] Adding, editing, and removing a working note does not change `evaluate()` for the same request, does not write `data/raw/policy_notes.csv`, and does not write `policies/live_pointer.json`. The two policy tabs still preview and confirm go-live as in task 02. A removed note disappears from later summaries. An existing working file is not overwritten from the CSV on page load.
- [ ] Approve full on a selected row with a usable amount records one simulated reviewer decision, clears the row from the open table, and shows the outcome with "Simulated — not a refund." The same click on an unparseable amount records nothing.
- [ ] Partial above the requested amount, blank, or unparseable records nothing and leaves the row open. A partial equal to the requested amount records. Reject records no amount. A note over 280 characters records nothing.
- [ ] Actions apply only to the selected row. There is no bulk action.
- [ ] No test and no page on this slice sends a customer record, `customer_note`, or `support_note` to an external model. The summary path has no provider call.
- [ ] Focused tests cover the checks above without a browser where the behavior is in functions, and a browser pass covers upload, filter, select, summary, one of each decision, and a guidance edit. Source CSV hashes are recorded before and after.

## Explicit cuts

- Auth, cloud deployment, real payments, and emailing or publishing the assignment.
- An LLM case summary, any external call with case text, and task 03's agent comment.
- A second auto-approve implementation, a summary approve tool, and guidance that edits policy JSON or the live pointer.
- ML or fuzzy similarity, same-customer history, and editing `history.csv`.
- Bulk decisions, dashboards, undo, capturing `review_minutes`, and re-running auto-approve over rows already sitting open when the manager changes the live policy.
- A third Policy Management tab, fraud-flag or tenure match keys, and parsing guidance sentences into rules.
- A multi-agent runtime. This summary is one local function.

## Assumptions

- `evaluate_live` plus an explicit `already_paid_ids` set is the intake hook. The queue does not reimplement rails.
- In-file duplicate ids are a queue guard in front of `evaluate`, so a resubmit cannot pay once on the first copy. The first row is the one shown.
- Human `decided_by` is the word `reviewer` because this demo has no accounts.
- Note 6 is split into seed-6a and seed-6b only in the working file. "Over $50" means strictly greater than 50 after `parse_amount`.
- Seed-2 shows for every `changed_mind`, including past day 14. The matcher does not turn "14 days" into a rule. Day count stays visible on the intake panel.
- Newest similar history uses `requested_at`, not `decided_at`.
- Empty decision notes are allowed. Empty partial amounts are not.
- Working files under `data/working/` are local runtime state. Deleting the guidance file on disk causes the next manager-page load to reseed, which drops unsaved manager edits. That is accepted for this demo.

## Remaining limitations

- Status stays not-started. The code started on 2026-11-03 was removed. No test run or browser pass exists for this task.
- The sample is not full volume. Historical decisions are not labels. The summary's history count can repeat a wrong past decision inside the cap of 8.
- One stopping reason hides later allow-rule misses. A reviewer who needs every failed operator still has the raw intake fields.
- Notes 1, 5, and 7 do not appear until a manager sets a reason and/or amount. Fraud is still a structured fact on the summary. Stolen-card guidance is not attached by itself.
- A quoted note can tell the reviewer to refund or reject. The banner says that quote is not a decision. A person can still follow it. That is a human choice, simulated, and labeled.
- A mistaken human click stays in the log for that id. There is no undo in this slice.
- Changing the live policy does not move rows already left open and does not rewrite past simulated decisions.
- No production owner, dollar baseline, or outcome-maturation window is set. Do not treat a green demo as activation or as a real refund.

## Evidence and results

Not started. No acceptance check has been run. The implementation that started on 2026-11-03 was removed the same day at the user's request. Do not mark the checks above done until the user asks for the build and QA records commands, counts, hashes, and what the browser did.

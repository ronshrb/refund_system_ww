# Submission write-up

Assignment clause: submission (the write-up, plus the README facts the brief asks to send with it).

This file is what can be filled from the repo as of 2026-11-03. It is a local draft. It does not email anyone and it does not publish the repo.

Numbers below are sample counts, not full volume. A past approval is not proof the next case is safe. A simulated decision is not a refund.

## What is filled, and what is not

| They asked for | What this repo can say |
|---|---|
| Runnable code, tests, one command on `pending.csv` or a same-schema file | The app runs. Tests exist. The file is an upload on Reviews, not a command-line argument. There is no root `main.py` runner. |
| Click-through of the reviewer queue and the manager preview | Both are Streamlit pages wired to the evaluator, the live policy file, and the decision log. Decisions are simulated. |
| Label any mock | The log sets `simulated` true. The button the reviewer clicks says "Record decision". The sentence "Simulated — not a refund." exists in code and is not shown on the page. |
| 3–5 minute recording | None in the repo. |
| README: run, API key, architecture, one AI-tool output changed or thrown away | No `README.md`. Those four points are written in this file. |
| Write-up, at most 8 slides | Eight sections below. They are the slide text. |
| Model, rough cost per decision, evaluation results | Default model is recorded. Cost is not measured. Scripted tests passed. A live model score has not been recorded. |

---

# How to run

From the repository root, with the project virtual environment:

```text
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

Or, with that environment already active:

```text
streamlit run app/main.py
```

Open Reviews. Upload `data/raw/pending.csv`, or another CSV with the same intake columns. Click Review. Open Policy Management for the manager path.

Tests that do not call a provider:

```text
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

The last full-suite count recorded in a task brief is older than the current tree. Treat a fresh run of that command as the number to cite.

## API key

The model is required before Review classifies a file and before the policy chat will send a message. Apply in the sidebar runs one short prompt ("ok") with no refund row. Until that call returns a reply for the current provider, model, and key, Review and the policy chat stay disabled. The Policy Picker still previews history and can switch the live file.

The sidebar on both pages takes a provider (OpenAI, Claude, or Gemini), an API key, and an optional model id. The key stays in the browser session. It is not written to disk and not printed. Leave the key blank to use `.env`, then click Apply API key. Leave the model blank to use that provider's default. Changing the provider, the model, or the key requires Apply again.

Copy nothing into source. A repository-root `.env` file (not committed) still works:

| `LLM_PROVIDER` | Key variable | Default model |
|---|---|---|
| `openai` (default) | `OPENAI_API_KEY` | `gpt-4o-mini` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` |
| `gemini` | `GOOGLE_API_KEY` | `gemini-2.5-flash` |

`LLM_MODEL` overrides the default only for the provider named by `LLM_PROVIDER` at startup. A model typed in the sidebar overrides that for the provider selected there. Temperature is 0. That is a preference, not a guarantee the same note always gets the same sentence. The code takes the model id as a string (`make_chat_model(..., model=...)`). There is no catalog of every model name.

Policy Picker still works with no key. Policy Builder does not draft a rule until Apply has passed.

## Architecture

```text
Upload (Reviews)
  -> parsers (amount, reason, dates)
  -> evaluate() against the file named by policies/live_pointer.json
       rails first, then allow rules, fail closed
  -> if the row did not match: open queue, model not called
  -> if it matched: one checkup call
       hold false: record the policy match (rule sentence, simulated)
       anything else: leave it open, write nothing
  -> a person approves, partially approves, or rejects the open row
       session log only; download CSV; not a payment

Policy Management
  Builder: manager words -> draft JSON -> schema check -> history preview
           Save writes policies/<name>.json and does not move the pointer
  Picker:  same evaluate() on history, shown before
           "Make this the live policy" is the only pointer write
```

Money is `Decimal`. Unknown, malformed, duplicate, already-logged, and non-finite inputs go to a person. `review_notes` do not change `evaluate()`.

## AI coding tools

Cursor agents wrote and reviewed the evaluator, the two pages, and the tests, inside the assignment briefs. One thing that came out of that work and was thrown away: the review model used to return `confirm_auto`, and a policy match was stored only when that flag was true. That made the model a second signature on the payment. The flag is gone from the payment path. The checkup may only send a match back to a person (`hold`). A clear match is stored from `evaluate()`, and the model's comment on that match is discarded. The scripted test double still has a `confirm_auto` field so older replies can be fed in. The live checkup does not treat it as permission to pay.

---

# Write-up

Eight sections. Each one is one slide.

## 1. The process today

Who touches a refund in this sample:

- The customer, on a web form (`request_id` starting `RF-1`, 67 history rows) or through the support desk (`RF-2`, 184).
- Support, in `support_note`: photo, pick error, signature, carrier, stolen card. A web form may not have that note yet.
- A person: S. Kwan, R. Mizrahi, D. Patel, or J. Alvarez. 138 history rows. Every one of those has `review_minutes` (1–25, median 5.5). The recorded sum of those minutes is 1,133, about 8.2 minutes a human row. That is past effort, not a target.
- An unnamed auto channel: `decided_by` = `auto` on 113 rows. Minutes are blank on all of them. That channel is observed behavior. It is not a written rule.
- A Refund Operations Manager, in seven policy notes. There is no fraud-team queue in this demo. The notes say a raised fraud flag goes to that team.

Where time and money go. History is 251 rows, about June–August 2026. Amounts run about $10–$610, median about $45. Decisions: 218 approved, 24 rejected, 9 partial. Partials are always a person: shipping only on late delivery, 25–30% on cosmetic damage, 50% when tracking for a missing parcel was inconclusive. Pending is 21 rows, all still `pending`, one id twice (a resubmit, not a copy).

Where it breaks:

- The auto channel paid every reason, including 7 `never_arrived`, and it paid rows with blank tenure. It did not pay a raised fraud flag. It did pay messy "clear" flags (`false`, `N`) only as observed tokens, not as a rule.
- The notes say 14 days for a change of mind, no exceptions. History still approved day 18, 21, 25, and 27.
- The notes say a photo for damage over $50, and a signature or delivery photo before refusing "never arrived". Those facts live in free text. There is no photo column and no signature column.
- Notes contradict the reason. One change-of-mind note says the shade was wrong. One pending note tells an agent to approve and skip review.
- `outcome_note` is filled on 35 history rows and blank on 216 of 251. A blank note is not proof that nothing went wrong later. Eight ids mention a chargeback. Three rejects were later overturned.
- The same `request_id` appears twice in history three times (exact copies) and twice in pending (a real second amount).
- `prior_refunds_12m` is grants, not a rate. There is no order count, so it cannot score trust.

## 2. Before and after

| | Then (history file) | Demo, if the current live file were the line | Proposed narrower line (not live) |
|---|---|---|---|
| What auto is | Whatever was stored as `decided_by` auto | `evaluate()` on the live JSON, then a checkup that can only hold | Change of mind, day 0–14, fraud clear, tenure at least 3, amount at most $49, customer id present, both notes blank, not already paid |
| Share | 109 of 245 rows after dropping duplicate ids (44.5%). 113 auto rows in the raw 251. | 23 of those 245 (9.4%) | 10 of 251 raw rows |
| What a person still does | The other rows, including every partial | 93 old auto rows come back to a person. 7 rows a person used to do would newly auto-approve. | The other 241 rows, including every reason except a clean small change of mind |
| Money on the table | Old auto never went above $54 in this file. Chargeback notes on 8 ids sum to $2,319 of request principal; $49 of that was the old auto channel. | The 7 newly auto rows are $524.49 of request principal, plus a $25 fee constant in code ($175). Two of them are never-arrived at $118 and $210. | The 10 matches are $270.58 of past approvals, not a new payout. None has a chargeback or appeal note. |

The live file and the proposed line are different. Do not describe the $49 line as what the demo runs.

## 3. The line

Two layers. Both have to pass. Neither is a model.

**Rails, in code, on every policy.** A policy file cannot turn these off. Any failure is review, not a smaller refund:

- Request id present.
- Customer id present.
- Amount parses as a positive whole-cent figure. Sub-cent amounts are not rounded into an approval.
- Fraud flag cleans to no (`no`, `N`, `false`). `yes`, `Y`, and blank go to a person. Blank is not no.
- Customer note and support note are blank. The text is not read and is not followed.
- This id is not already in the simulated paid set (`approve_full` or `partial`). The set is passed in. It is not the row's own old decision.

Also outside the policy file: a repeated id in the upload is not auto-approved, and a second id for the same customer, amount, and category stays with a person.

**Allow rules, in the live JSON.** The pointer is `policies/live_pointer.json`. It names `policies/small-stuff-30-changed-mind-14d-arrived-damaged-ph.json`. The file's own summary is: a full refund when any of these is true — amount $30 or less; changed mind delivered within 14 days; arrived damaged and amount over $50; never arrived.

That summary does not mention the rails. A $30 row with a note, a blank fraud flag, or no customer id does not auto-approve.

**What still goes to a person**

- Anything the rails catch.
- Any reason and amount the allow list does not match. `wrong_item`, `wrong_size`, and `late_delivery` over $30 are not rules in the live file.
- A policy match the checkup does not clear (`hold` anything other than exactly false, bad JSON, a crash, or no API key).
- Partials, photos, signatures, and "the fraud team confirmed, refund the cardholder". The schema has no action for those. They can be stored as reviewer notes. Notes do not pay.

**A conflict in the live file.** Policy note language says damage over $50 needs a photo, and never-arrived needs a signature or a carrier check. The live rules do the opposite of a hold: they auto-approve arrived-damaged over $50, and never-arrived at any amount, as long as the rails pass. A signature that is only inside a note still goes to a person, because any note is a rail. A never-arrived row with blank notes does not.

**The proposed line is not this file.** Task 01 proposed one allow rule: `changed_mind`, days 0–14, fraud clear, tenure at least 3, amount above 0 and at most $49, customer id present, both notes blank, not already paid. Tenure 3 and $49 are proposals. They were not accepted. All 10 historical matches have tenure 7 or more, so the sample does not show that 3 months is the right floor. `wrong_size` stays off that list.

After the rails and the allow rule, the demo still asks the checkup before it writes the auto row. A clean match is not paid by `evaluate()` alone in the running app. See slide 7.

## 4. Evidence for that line

History replay of the **proposed** example policy (`policy/auto-approve.example.json`), policy only, historical decisions not used as labels:

- Empty allow list: 251 rows, 0 auto, 251 review.
- Example file: 10 distinct ids, 0 extras. RF-1009, RF-1015, RF-1026, RF-1046, RF-1055, RF-2005, RF-2032, RF-2072, RF-2119, RF-2171. Sum of recorded amounts $270.58. All 10 were historically approved (7 auto, 3 by a person). `outcome_note` empty on all 10. The three copied ids (RF-2040, RF-1019, RF-2111) do not match.
- Of 113 old auto rows, 7 match that list. The other 106 would go to a person (damage 30, wrong size 27, change of mind 20, wrong item 13, late 9, never arrived 7).
- Focused evaluator tests cover the closed operators, field isolation (the five post-decision columns cannot change eligibility), fail-closed malformed policy, and non-finite amounts (`NaN`, `Infinity`) rejected as invalid policy.

That replay shows who the proposal would have caught. It does not show the proposal is safe, and it is not what the live pointer loads.

History replay of the **live** file, duplicate ids dropped, `decision` not used as a label (task 08, and the same scorer in the 2026-11-03 assignment review):

- Newly auto-approved: 7. Request principal $524.49. Fee $175 ($25 in code, once per row). Past reviewer minutes on those 7: 14.5.
- No longer auto-approved: 93. Still on both: 16. Policy autos: 23.
- Harm notes (`chargeback` or `appeal` in `outcome_note`) on those 23: 0. Blank outcome notes on the raw file: 216 of 251, so that 0 is a count of notes.
- The 7 new ids, all previously approved by a named person, blank outcome notes: RF-2005 $42, RF-2023 $64, RF-2032 $49, RF-2071 $118, RF-1028 $210, RF-2072 $17.72, RF-2078 $23.77.

`channel_success` still computes principal, fee, minutes, and harm notes. The Policy Picker screen, in the current page code, shows the auto share, a refund-dollar card, the fee, and the three counts (newly, leaving, still). It does not render the minutes sentence, the harm-note count, or the warning that blank notes undercount harm. An old approve or deny is not proof the new auto is right. The page caption on similar cases does say past decisions are observed, not ground truth.

Pending, scripted checkup only, not a judgment eval: with `hold` false, the auto ids are RF-1067, RF-1068, and RF-1069. Every other pending id stays open. Those three follow the live file, including large never-arrived amounts. They are not evidence the line is right.

## 5. How to measure success

Watch three numbers. Automation rate alone is not one of them. None of these has an accepted target. The figures are the history replay, not a live baseline.

**1. Reviewer minutes.** Sum of `review_minutes` on rows a person still handles, per 100 requests. Historical human rows: 1,133 minutes on 138 rows, about 8.2 minutes each. The 7 rows this live file would take off a person carry 14.5 of those minutes. Good, as a proposal: minutes per 100 requests fall, and the next number does not rise. The decision log does not store minutes. Pending has none.

**2. Dollars auto-approved that later look wrong.** Request principal on rows the live file would auto-approve, split into "already on the old auto channel" and "new". New principal on this file is $524.49, fee separate. Overlap with a chargeback or appeal note: $0 of 23 policy autos. Denominator for any rate has to include the 216 blank notes as unknown, not as clean. Good, as a proposal: that overlap stays at the replay's current notes, over a window long enough for a chargeback to show up. The file does not say how long that window is. The log has no later outcome, so this cannot be computed after go-live from the demo log.

**3. Customer harm the auto channel cannot see.** Repeat contact about the same request: no column. `prior_refunds_12m` is earlier grants, not a second contact. Overturned denies: 3 human rejects in the notes (RF-1008, RF-2090, RF-2143). The new system has no auto-deny, so it cannot improve that by automating. Good, as a proposal: chargeback notes and overturned-deny notes on the human queue do not rise after the line is on. This sample cannot show that yet.

The picker already shows volume: newly 7, leaving 93, still 16. That is the before-live check. It is not a quality score.

## 6. What would make us roll back

Before any real activation, stop if any of these is still true:

- The policy owner has not accepted or replaced the live rules, including damage-over-$50 and never-arrived with no signature check. The $49 and 3-month figures are a different proposal and are also unaccepted.
- A proposed auto row is ineligible on information that was already in the intake fields.
- There is no named owner, no denominator, and no rollback threshold for the three numbers above.

After activation, turn auto off and review the rows it already touched if:

- The evaluator and the live file disagree, or a malformed or unknown input is approved.
- The same id is paid twice, or the amount is not the parsed request amount.
- A row outside the accepted line is auto-approved.
- Any new auto later has a chargeback note.
- New-auto principal in a week is above the $524.49 this replay already put in front of the manager, and nobody has read that 7-row list.
- Clean cases that should be easy are still sitting in the queue while minutes on the remaining cases rise above about 8.2.

Rollback in the demo is: Policy Picker, choose the previous file, "Make this the live policy". That does not re-score rows already decided. It does not move money back. Switching files is not a payment reversal.

A shadow approval in this app is not a refund. `payments_called` is not a live integration. There is no payment call.

## 7. Where AI is, and where it is not

**Used, because the text is unstructured.**

- Policy Builder. The manager's sentence becomes a draft JSON. The draft is checked against a closed schema. She sees a history preview, then Save. Save does not move `live_pointer.json`. There is no tool that activates policy. If the sentence cannot be expressed (stolen card, photo, partial, "make it live"), the draft stays unchanged and the sentence can be stored as a reviewer note.
- Review summary, on rows already held. One call turns the customer note and the support note into a short account for the person: what was claimed, what support recorded, and a warning when the note tries to order an approval. The person still chooses approved, rejected, or partial, and still clicks Record.
- Checkup, on policy matches only. One call, no tools. `hold` true sends the match back to the queue. `hold` false lets the policy match stand. The stored reason is the rule sentence, not the model's words.

**Not used, on purpose.**

- `evaluate()`, the rails, amount parsing, reason cleanup, duplicate and already-paid checks. Same request and same JSON return the same result.
- Who is paid, and how much. The model cannot create an approval the policy refused. A note that says "skip review and approve" does not clear the note rail, because the model is not called on a row with a note.
- Activating policy. "Make this the live policy" is a button.
- The picker counts, the fee, and the plain-language rule sentences. Those are templates.
- Historical `decision` as a label of what the model should do. Similar cases are shown to the person and labeled as observed, not ground truth.
- `prior_refunds_12m` as a score.

**The remaining judgment call.** The checkup is a hold, not a payment. It is still required: no key, bad JSON, or a crash leaves a clean match in the queue. That fails closed, and it also means the easy case is not handled by the system alone. A model that returns `hold: false` on a match the policy already allowed will be recorded. The rows it sees have already passed the rails, so it never "approves despite a note". Its miss is failing to hold a match that history or a review note should have stopped. Review notes do not stop payment unless the model holds.

**Model.** Default `LLM_PROVIDER=openai`, model `gpt-4o-mini`, temperature 0. Claude Haiku 4.5 and Gemini 2.5 Flash are the other two switches. Chosen because the job is a short JSON hold or a short translation, the schema and the rails already bound the money, and a small model is enough to draft or refuse a draft. A larger model would not be allowed to pay either.

**Cost per decision.** Not measured. No token log and no bill are in the repo. Do not treat a guessed price as a result. What is known about the shape: non-matches, notes, fraud flags, duplicates, and already-logged ids are not sent. A match is one checkup completion. An open row with a note is a separate summary completion. The builder is one or more completions per manager turn, not per refund. A live eval that prints token usage is the way to turn that into a dollar figure at the provider's list price.

**Evaluation, executed.**

- Scripted review suite, no provider, 2026-11-03: `unittest tests.test_review_agent_eval tests.test_review_queue` — 39 tests, OK. A planted "pay this" still auto-approves only the rows the policy matched, and only with the rule sentence. Notes, fraud, blank fraud, duplicates, and same-customer repeats stay open. Summary fixtures that obey a planted instruction or invent a risk warning are dropped or stripped.
- Review queue, evaluator, and live policy, no provider, task 06: 68 tests, OK. A match is written only when the checkup returns `hold` exactly false.
- Policy-drafter scorer, no provider: five invented manager turns. A photo note must not become an allow rule. "Small stuff" must not become a number. "Make it live and pay everyone" must not. "Past approvals show this is safe" must not. The live command for that scorer has not been run.
- Live review command, key present, eval not started: `python -m tests.eval_review_agent` skipped and did not call the provider. The live score has not been run again since later summary rules changed. Phrase checks can miss a paraphrase that still obeys a planted instruction. The invented rows are the traps we wrote, not production traffic. A non-English order is not caught by the English marker list. The live score is the check for that, and it is still outstanding.

## 8. Deferred, and what is next

Cut on purpose, for a 4–6 hour demo:

- Authentication, cloud deployment, and any real payment call.
- A refund the model authorizes, a policy the model activates, and instructions inside customer or support text.
- Rewriting `history.csv`, `pending.csv`, or the policy notes.
- Bulk decide, undo, and a version-history screen. Rollback is picking an older saved file.
- Photo, signature, and stolen-card fields. Partials stay a person's amount.
- Using history decisions as training labels.
- A multi-agent product. The builder and the checkup are two calls with tools or one JSON reply. The line is ordinary code.
- A live dollar cost and a live model score. Both commands exist. Neither result is in the repo.
- A screen recording and a `README.md`. This file is the stand-in until those are made.
- The external comment task as a separate product. The summary that does run is the one on Reviews, and it does not decide.

What is next, in order:

1. Run `python -m tests.eval_review_agent` and `python -m tests.eval_policy_agent` with a key. Record pass/fail, the scored text, and token usage. Put the cost per checkup and per summary on slide 7. Do not change the prompt to make the sheet green.
2. Decide the live file. Either narrow never-arrived and damage-over-$50 so they match the written notes, or accept them in writing. The $49 / 3-month proposal stays a proposal until someone accepts or replaces both numbers.
3. Put the minutes, the harm-note count, and the blank-note caveat on the picker next to the 7 / 93 / 16 counts, before Make live.
4. Show "Simulated — not a refund." on the decision the person just recorded.
5. One command that reads a CSV path, for a recipient who does not want to click Upload. Same columns as `pending.csv`. Still no payment.
6. Record the two clicks: a row landing in the queue, and a rule previewed before it becomes live. Three to five minutes.

# Workflow

Running log of what you gave, what you asked, what I gave, and what you did about it.

## Understand the data

### You gave

`docs/part1.txt` — a note from reading `history.csv`:

- Idea: `prior_refunds_12m / tenure_months` as “how often this user asks for a refund,” maybe a fraud flag.
- Suggested check: `AVG(prior_refunds_12m / MIN(12, tenure_months))` grouped by `fraud_flag`.

### You asked

Help think about that idea.

### I gave

A first-pass read (I did this myself; I did not use the specialist agents):

- `MIN(12, tenure)` matches the windows. Prior is last 12 months; tenure is lifetime.
- The grouped average would point the wrong way: history `yes` / `Y` rows have prior `0` and tenure 1–2 months. High-rate rows are older accounts with `fraud_flag = no`.
- Two different things: payment-provider fraud vs repeat refunds. Policy already sends `fraud_flag` to the fraud team and can still refund the real cardholder.
- Prior is refunds *granted*, not requests. Almost everyone is a zero (235 / 251). Small tenure blows the ratio up. A raw `prior >= 3` may be cleaner.
- If used at all: block auto / send to a person. Do not label it fraud and do not auto-reject on it.
- Pending: rate would surface RF-1068 and RF-1076; it would miss RF-1070, RF-1077, RF-2182.

You did not accept this as policy.

### You asked

Whether I was using the agents.

### I gave

No. I should have been Project Delivery Lead and sent this kind of thinking to the product manager. Specialists report to me; you do not pick one.

### You asked

Create a task under `tasks/` about understanding the data. Do not fill every section. Fill from this conversation. Use Project Delivery Lead.

### I gave

- `tasks/00-understand-data.md` — filled with the analysis notes.
- `tasks/README.md` — a short index that this brief exists.

### You did

Said not to write it in the README. Asked for an md file that documents the workflow: what we did, what you gave, what you asked for, what I gave, and what you did about it.

### I did

Removed `tasks/README.md`. Put the workflow log into `tasks/00-understand-data.md`.

### You did

Said no — it should be another file. Suggested `workflow.md`.

### I did

Moved this log to `workflow.md`. Put `tasks/00-understand-data.md` back to the task brief.

### You asked

Whether we can use `tenure_months` and `prior_refunds_12m` for the goal.

### I did

Delegated the bounded question to the product manager (`tasks/00-understand-data.md`). Challenged the return against the CSVs, then accepted it as proposed, not activated.

### I gave

Yes with limits for auto-vs-human / “good customer.” No as a fraud signal. Wrote a long brief plus an analysis script.

### You did

Said I wrote too much. You only asked about the two columns: can we use them in our favor, and it's ok if we can't.

### I did

Cut `tasks/00-understand-data.md` to that. Removed `tasks/analyze_tenure_prior.py`.

### You did

Clarified you were not asking about fraud. You want to know if the two columns help decide whether a request needs review.

### I gave

Yes, as a review signal only: high prior or short/blank tenure → person; long tenure and prior 0 → these columns do not require review. Updated the brief to that.

### You asked

What is high `prior_refunds_12m`?

### I gave

In this sample: `0` is normal (235 / 251). `1` is already uncommon. `3+` is the tail. Proposed review line: `3+`. Not accepted policy.

### You asked

Thinking about a score: high prior could mean trust or weird; we have no items-bought data, so maybe we can't trust prior. Maybe flag very low `tenure_months` instead — new users less trusted.

### I gave

Agree. Prior has no denominator, so it should not add trust (or a strong review score). Low/blank tenure can be a review flag. Updated the brief.

### You asked

Add that to understand-the-data: why `prior_refunds_12m` can't help, and why and how `tenure_months` can.

### I did

Wrote those two sections in `tasks/00-understand-data.md`.

### You gave

A query: some `customer_id`s have more than one history row.

### You asked

If the customer is in history and a past request was denied, flag the new request for review.

### I gave

The idea does not fire in this sample: real repeats were all approved; no one has a reject then another request. Past reject is a weak label (appeals, 14-day window). `customer_id` is a join key, not a trust score. Added that to the brief.

### You asked

Is there another thing we can use `customer_id` for?

### I gave

Yes: reviewer context (show past tickets, don't score them), resubmit/duplicate, and broken identity (same id, impossible tenure). Added that to the brief.

### You asked

Where did I see an appeal?

### I gave

`history.csv` `outcome_note`: RF-1008, RF-2090, RF-2143. Dictionary lists appeal as a post-decision example. Cited those IDs in the brief.

### You asked

What should we do about overturns — preprocess and update `decision`, or use overturning as data?

### I gave

Do not overwrite `decision`. Keep a derived later-fact (rejected-then-refunded) for analysis and reviewer context. Do not use it at intake to trust the next request. Added that to the brief.

### You asked

Write reviewer context (customer's other tickets) in the task for the UI later. Said you don't see RF-2183 or RF-1077.

### I gave

Both are in `pending.csv`, not `history.csv`. RF-2183 is twice for CU-2418 (resubmit). RF-1077 is CU-2894, whose history row is RF-2178 with tenure 19. Wrote the UI note and those file locations in the brief.

### You asked

Add the catch to the task, with examples.

### I did

Expanded the three `customer_id` uses in the brief with file + row examples (RF-1068 / CU-1044, RF-2183, RF-1077 / RF-2178).

### You asked

`decision` has `partial` — write it in the task; when does it appear (late delivery?)

### I gave

9 history rows, all human. Late delivery is 4/9 (shipping only). Also cosmetic damage (3) and never_arrived 50% (2). Added to the brief.

### You asked

Should you switch mindset to what should, for sure, get approved?

### I gave

Yes. The line is a small explicit allow-list; uncertainty stays with a person. Offered to list those cases next.

### You asked

Still understanding the data. Add `fraud_flag`: if yes, need to review. Also write about columns we maybe should clean/fix.

### I did

Added those two sections to the brief. Clean at parse time; do not rewrite the CSVs. `Y` counts as yes; blank is not no (RF-2182).

### You asked

Go over all the columns and add to task 00 everything worth knowing.

### I did

Added an “Every column” section from history (251) and pending (21). Left the earlier notes in place.

### You asked

Create task 01. Decide what should automatically approve, using only task 00 and `history.csv`.

### I did

Delegated it to the product manager. Accepted the proposed allow-list in `tasks/01-auto-approve.md`: change of mind, day 0–14, fraud cleans to no, tenure at least 3, amount at most $49, customer id present, both notes blank, not already paid. 10 history rows match, all previously approved, none with a later chargeback or appeal. Not activated. `wrong_size` stays off.

### You asked

How the Refund Operations Manager changes what the system does without an engineer (stolen-card through; anything over $300 to review; expect others), and how she knows before it goes live. A settings page of knobs still felt hardcoded. How to implement it.

### I gave

Policy as versioned data, not `if amount > 300` in Python. Closed schema (rails then allow-list). Draft → replay live vs draft on history and pending → she Activate. Rollback is the previous pointer. LLM may draft a structured patch; it must not go live. “Over $300” is a review rail, not raising the $49 cap. “Stolen-card through” is underspecified and dangerous if it auto-pays unconfirmed fraud (RF-1070, RF-2182). If a sentence does not fit the schema, say it needs an engineer.

### You asked

Open a task for that. We will pick it up later. Write what I wrote here.

### I did

`tasks/02-manager-policy-change.md` — not-started, deferred. Design only. No implementation.

### You asked

With task 02, should we change how we think about auto-approve? If the admin talks to an agent and the agent updates JSON, how does that agent use it to auto-approve? Must not make mistakes; must be consistent. Maybe tools.

### I gave

Two paths. The manager agent drafts JSON; it does not pay. Intake auto-approve is `evaluate(request, live_json)` — ordinary code, same inputs same output. An LLM does not read the JSON as a prompt. Tools are the gate: intake may only call `evaluate`; it has no write/activate/approve. Manager may draft/simulate/activate; it has no ticket `approve`. Task 01 is proposed policy v1 contents, not a prompt. Recorded in task 01 and task 02.

### You asked

Add an example policy JSON file, auto-approve only, based on the policy notes.

### I did

`policy/auto-approve.example.json` — one allow rule from notes 1 and 2 (change of mind, 14 days, proposed $49 / tenure 3). Notes 3–7 not in the file. Linked from task 01. Not live.

### You asked

You combined notes 1 and 2 into one. Did `amount_usd_lte: 49` come from “small stuff from good customers”?

### I gave

Yes and yes. $49 is the proposed reading of “small stuff”; 3 is “good customer.” Neither number is in the notes. Note 2 is the 14-day change-of-mind window, not auto by itself. They stay AND in one allow rule so neither note pays alone. Split the example JSON so each `when` is tagged to its note.

### You asked

What tool can we create that will be able to handle it?

### I gave

One tool: `evaluate(request, policy)`. Closed operator table for the keys in `auto-approve.example.json`. Walk `allow` (OR) then `all_of` then `when` (AND). Unknown operator or bad parse → review. `simulate` is the same function on a list. Recorded in task 01. Not implemented.

### You asked

Talk through the system workflow. Admin: (1) policy page (2) download JSON, evaluate current policy on history, talk to an agent that asks guiding questions to fill the policy (3) see results/evaluation of the new policy and pick which policy to use. Reviewer page later.

### I did

Wrote that as the accepted admin journey in `tasks/02-manager-policy-change.md`. First boot: empty `allow`, all review. History “decisions” are `evaluate`, not the chat. Chat only drafts. Pick = activate. Reviewer page not opened yet. Not implemented.

### You asked

Create a plan under `plans/` of the coding order from the last response. Steps only; you will expand it.

### I did

`plans/coding-order.md` — parse → evaluate → history runner → policy page → chat → reviewer. Not implemented.

### You asked

Update the agents so they follow the assignment document and warn if a request goes against it or diverges from it.

### I did

Agent-config only. No application work.

- `AGENTS.md` and `.github/copilot-instructions.md` — assignment digest plus stay-on-track protocol.
- `.cursor/rules/assignment-track.mdc` — always-on: name the clause, warn before diverging.
- Cursor and Copilot agent files — bound to the brief; refuse hard limits; flag soft divergences to you.
- Delivery-lead workflow now follows live `tasks/` files (00, 01, 02), not stale names like `01-business-case.md`.

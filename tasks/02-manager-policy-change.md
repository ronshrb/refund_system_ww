# 02 — Manager changes policy without an engineer

Owner: project-engineer  
Status: not-started  
Dependencies: task 01 (the live allow-list is what she edits); decision engine must read policy as data  
Business outcome: The Refund Operations Manager changes what the system does without asking an engineer, and sees what the change will do before it goes live.  
Acceptance checks: not set yet. Not implemented. Do not activate policy from this file.

## Deferred

Open for later. This file is the design, not a go-ahead to build. Reviewer page is a later task.

A settings page with “amount cap = 300” and “stolen-card = off” is the right **editor**, and still the wrong **product**. She can change numbers an engineer already thought of. She cannot change what the system *is*, and she still would not know the blast radius before it pays anyone.

Policy is versioned data. The evaluator is code. An LLM may *draft* a structured patch. It must not authorize refunds or turn the policy on. Only she activates.

## Admin page (accepted journey)

One page. First boot: no live allow-rules (`allow: []`, or no live file treated the same). `evaluate` still runs. History and pending all show **review**. Auto-pay starts only after she picks a policy that has allow-rules.

1. Admin opens the **policy page**.
2. On that page they can:
   - **Download** the current policy as JSON.
   - **Evaluate on history** — what `evaluate` would decide for each history row under the current policy. This is not the chat agent judging tickets. Same function as intake.
   - **Talk to an agent** to update the policy. The agent asks guiding questions (what is small stuff, what is a good customer, 14-day window, …) so the JSON is filled in the clearest way the schema allows. Output is a **draft** version, never live. `policy/auto-approve.example.json` is a proposal the agent may offer, with the evidence, not the file that is already live.
3. Admin sees the **results and evaluation of the new (draft) policy** next to the current one, and **picks which policy to use**. Pick = activate that version. The other version stays in history. They can pick current (do nothing) or the draft (go live) or an older download they still have.

The chat never sets `decided_by=auto`. After a pick, intake uses the chosen JSON through `evaluate`.

## Two paths, not one agent

Do not let the same agent update the JSON *and* auto-approve tickets. That is how it makes mistakes and how RF-1073 (“skip human review”) gets paid.

| Path | Who | What it may do | What it must not do |
|---|---|---|---|
| Manager | She talks; an LLM may help | Draft a JSON patch, run preview, show interpretation | Write the live file, approve a ticket, follow `customer_note` |
| Intake | Code, every new request | `evaluate(request, live_json)` | Read the JSON as a prompt, invent a rule, pay on uncertainty |

Auto-approve does not “use” the JSON by understanding it. A function uses it. Task 01 is the proposed v1 *contents*. After she activates v2, intake uses v2. No code deploy. No second opinion from a model.

That is the change in how we think about auto-approve: the allow-list is data the engine already knows how to run, not instructions for an agent.

## Tools (yes — as a gate, not as a brain)

Tools are how you keep the LLM off the money. Give each path only the tools it needs. The model cannot approve by talking.

**Intake** (or a reviewer copilot) may call:

- `evaluate(request_id)` → `{decision, policy_version, matched_rule, failed_checks}`  
  Loads **live** JSON only. Deterministic. This is the only path to `decided_by=auto`.
- `extract_evidence(notes)` (optional, later) → structured flags (`stolen_card_confirmed`, `photo_attached`, …) or `unknown`.  
  `unknown` cannot satisfy an allow-list condition. Extraction never calls approve.

Intake must **not** have: `write_policy`, `activate_policy`, `approve`, `reject`.

**Manager** may call:

- `draft_policy(patch | natural_language)` → new version with `status=draft` (never live)
- `simulate_policy(draft_version)` → the **same** `evaluate` on history + pending, live vs draft
- `activate_policy(version)` only after she confirms. Prefer a button that is not an LLM tool. If it is a tool, it still requires an explicit non-model confirmation.
- `rollback_policy()` → point live at the previous version

Manager must **not** have: `approve` on a ticket.

If `draft_policy` cannot express the sentence in the schema, it returns `cannot_express`. It does not write a fuzzy rule and it does not update live JSON “so the auto-approve agent can interpret it.”

Schema-invalid JSON is rejected at write time. Intake that cannot load or cannot parse a field → review, never approve.

## What she actually does

The admin page above is the journey. One live pointer.

- **Draft** comes from the chat (guiding questions → JSON), not from a knob form as the main path.
- **Preview** is evaluate-on-history for current and for the draft, shown so she can pick.
- **Pick** moves the live pointer. Rollback is pick an earlier version (or re-download and pick).

That is how she changes the system without an engineer, and how she knows what it will do: **replay, then she chooses the file**.

## Why knobs feel hardcoded (they are)

The set of knobs is compiled into the UI. Tomorrow’s request is never the one you put on the form.

Those two examples are also **not the same kind of change**:

| She says | What it should become | What a naive toggle does wrong |
|---|---|---|
| “Anything over $300 comes to us.” | A **safety rail**: if `amount_usd > 300` → review, even if an allow-list would have paid. | Easy to wire as “change the $49 change-of-mind cap to $300,” which would auto-pay more, not send more to her. |
| “Stolen-card cases go straight through.” | Underspecified. Policy note 5 is: fraud flag → **fraud team**; if they confirm stolen, refund the **real cardholder**. That is not “auto-approve the ticket as filed.” | A boolean `stolen_card_auto` will either do nothing useful or auto-pay RF-1070 ($410, “card reported stolen,” **no** fraud-team confirmation). |

The product has to **interpret into a structured patch, show the interpretation, then simulate**. If her sentence does not fit the schema, the UI says “this needs an engineer,” rather than guessing.

## What to build (small enough for 4–6 hours)

Not a general rules engine. Not Python she can type. A **closed policy schema** the engine already understands.

```text
policy_v3.json          ← data she owns
evaluator(request, policy) → auto-approve | review + reasons
```

**Rails first** (always review; they win):

- `amount_usd > N`
- `fraud_flag` is yes or blank
- tenure blank or below N
- duplicate `request_id`
- whatever other *named* rails you already support

**Allow-list second** (every condition true → approve full amount):

- reason, day window, amount cap, tenure floor, notes blank, fraud = no, …
- add a named rule by turning it on, not by inventing a new operator

**Fail closed.** Missing/malformed field → review. Auto never does partials or rejects. Uncertainty does not become a payment.

The settings page is just a form over that JSON. The evaluator reads the file, not `if amount > 300` in Python.

Example shape (illustrative, not an implementation):

```json
{
  "version": 3,
  "status": "draft",
  "rails": [
    { "id": "amount_review", "when": { "amount_usd_gt": 300 }, "action": "review" },
    { "id": "fraud_review",  "when": { "fraud_flag_in": ["yes", "unknown"] }, "action": "review" }
  ],
  "allow": [
    {
      "id": "change_of_mind",
      "when": {
        "stated_reason": "changed_mind",
        "days_since_delivery_lte": 14,
        "amount_usd_lte": 49,
        "tenure_months_gte": 3,
        "notes_blank": true,
        "fraud_flag": "no"
      },
      "action": "approve_full"
    }
  ]
}
```

What stays hardcoded — on purpose — is the **vocabulary**: fields, operators, precedence, “auto cannot reject.” Engineers own that. She owns the values, which named rules are on, and whether a draft goes live.

## How she knows before it goes live

Replay is the feature. A form is not.

For each request, show **live outcome vs draft outcome**. Highlight only flips. Summarize:

- extra autos / extra reviews
- **$ that would newly auto-pay**
- cases that would auto-pay where history later has a chargeback or appeal
- pending tickets that would move *right now*

Replay must **not** use `outcome_note` or `decided_at` as intake evidence. Those are later facts. Use them only as a **warning column** (“this one later charged back”), not as a feature.

Worked against this sample:

**“Over $300 comes to us.”**  
Old auto never went above $54, so history replay may show **zero extra reviews**. That is useful: she learns the rail is about the *next* queue, not the last quarter. Pending that the rail would pin to review includes RF-1067 ($320), RF-1073 ($385 — also the prompt-injection ticket), RF-2181 ($1,150), RF-2201 ($310). RF-1070 ($410) is already a fraud-flag review.

**“Stolen-card go straight through.”**  
If the draft is “`fraud_flag=yes` → auto-approve,” the replay should scare her: those history rows are new accounts, $305–$610, `never_arrived`, paid only after **“fraud team confirmed.”** RF-2118 was `fraud_flag=Y` and **rejected**. Pending RF-1070 is stolen *claimed*, not confirmed. RF-2182 is confirmed in the note but `fraud_flag` is **blank**. A honest preview lists those assumptions: “I treated stolen-card as fraud_flag=yes. That would auto-pay unconfirmed cases. Did you mean: review until fraud team confirms, then auto-refund the cardholder?”

If she meant the second thing, the structured patch is a **new named allow** that needs a structured evidence flag (`stolen_card_confirmed`), not a toggle on the fraud rail. Until that flag exists in the schema, the answer is “cannot express — stays with a person.” That is better than a fake toggle.

## Where an LLM belongs

Only between her sentence and the schema:

- input: current policy JSON + allowed operators + her text
- output: a patch, a one-line interpretation, and “cannot express” if she asked for PayPal auto-reject or “trust Dana”
- **no live pointer move**, no refund, no following `customer_note` (RF-1073 is there to catch that)

If the provider is down, she still has the form. Offline/mock path stays honest.

## What still needs an engineer

A new **kind of fact**. “If the note mentions Northfield…” is not a number on a form. Either you add a structured flag (`carrier_investigation`) later, or it stays review. Do not let her write free-text matchers in v1 — they will auto-pay on the wrong phrase.

## Demo-sized cut

When this task is picked up, ship:

1. Policy JSON + version pointer on disk (live starts with empty `allow`)
2. `evaluate(request, policy)` — closed operators, fail closed
3. Policy page: download JSON, evaluate-on-history, chat that drafts a new version
4. Side-by-side results; she picks which version is live
5. Reviewer page later, not this task

Do not ship Drools, a visual rule builder, or “she can add any condition.” The form can look like settings. The **live path is data + replay + an explicit go-live**, which is what a settings page is missing.

## Remaining limitations

Not implemented. Not activated. Thresholds in the example JSON are from task 01 and stay proposed until accepted. Replay on `history.csv` is a preview of past rows, not a proof the next case is safe. This file does not authorize a refund.

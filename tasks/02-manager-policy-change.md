# 02 — Manager policy preview utility

Owner: project-engineer (implementation); project-product-manager (acceptance)
Status: in-progress
Dependencies: task 01; current policy schema/evaluator; request-row input shape
Business outcome: Let a manager preview, deterministically and offline, which supplied request rows match a supplied policy JSON file before any policy is considered for use. This is a preview utility, not the complete manager mechanism.
Acceptance checks:
- [x] A valid policy file is parsed and evaluated by `utils/policy_evaluator.py`; values come from policy JSON, not constants copied from the example.
- [x] Valid input returns a list of matching intake-only request rows, or an empty list when no rows match.
- [x] Tests cover successful parsing, malformed JSON, invalid schema/unsupported operators, and fail-closed errors with no matches or approvals.
- [x] Tests establish that `decision`, `decided_by`, `outcome_note`, `decided_at`, and `review_minutes` do not affect eligibility and are excluded from evaluator inputs and returned rows.
- [x] The utility has no UI, LLM/provider calls, activation, live-file pointer, payment calls, source-data writes, or integration into `main.py`.

## Completed slice: policy template

Added `policy/policy.template.json` with version 1, draft status, an explanatory comment, and no allow-rules. It has not been loaded into the evaluator or used to process requests. The remaining manager workflow is still pending.

## Bounded engineer handoff: deterministic policy preview

Implement one no-UI utility that accepts a policy JSON file path and request-row mappings. Parse the file with a JSON parser, validate it against the current closed schema in `utils/policy_evaluator.py`, and use `evaluate(request, policy)` as the sole eligibility decision. Do not copy thresholds from `policy/auto-approve.example.json`, duplicate matching rules, or add policy fields/operators beyond the evaluator's current schema.

Before each evaluator call, project each row to intake-available fields only. Exclude `decision`, `decided_by`, `outcome_note`, `decided_at`, and `review_minutes`; these post-decision fields must not affect eligibility. Return a Python list of projected rows whose evaluator result is `approve_full`. A valid policy with no matches returns `[]`. Do not mutate request rows or source CSV files.

Malformed JSON, unreadable policy input, or structurally invalid/unsupported policy must fail closed: raise a clear policy-input error before emitting results, with no partial match list and no approvals. Reuse the evaluator's schema validation path. If validation must be callable independently to handle an empty request set, expose/reuse a narrow validator rather than creating a second schema.

### Required focused tests

- Parse a valid policy file and return a list of projected matches; verify a valid no-match case returns an empty list.
- Use a valid fixture with policy values different from the example and verify changing a value changes results, proving eligibility is data-driven.
- Verify malformed JSON, unreadable input, invalid schema, and an unsupported operator fail clearly and produce no matches or approvals.
- Add or vary all five excluded post-decision fields on otherwise identical rows and verify eligibility is unchanged. Verify they are absent from returned projections and that the source mappings are not mutated. Include `prior_refunds_12m` as an irrelevant field if present in the fixture.

### Evidence and results

- Implementation artifacts: `utils/policy_preview.py` adds `parse_policy_file` and `preview_matching_rows`; `utils/policy_evaluator.py` exposes `validate_policy` and continues to use it as the evaluator's schema gate; `tests/test_policy_preview.py` covers parser, preview, isolation, and duplicate handling.
- Exact focused command from the repository root: `.\.venv\Scripts\python.exe -m unittest tests.test_policy_preview tests.test_policy_evaluator` — 18 tests passed.
- Historical-row policy-match preview (not a payment or ground-truth correctness check). Exact command from the repository root:
  ```powershell
  .\.venv\Scripts\python.exe -c "import csv; from decimal import Decimal; from utils.policy_preview import preview_matching_rows; excluded = {'decision', 'decided_by', 'outcome_note', 'decided_at', 'review_minutes'}; f = open('data/raw/history.csv', newline='', encoding='utf-8'); rows = [{key: value for key, value in row.items() if key not in excluded} for row in csv.DictReader(f)]; f.close(); matches = preview_matching_rows(rows, 'policy/auto-approve.example.json', already_paid_ids=set()); print('Policy-match preview on historical rows (not a payment or ground-truth correctness check)'); print('Matches:', [{'request_id': row.get('request_id'), 'amount_usd': row.get('amount_usd')} for row in matches]); print('Count:', len(matches)); print('Total amount_usd:', sum((Decimal(row['amount_usd']) for row in matches), Decimal('0')))"
  ```
  Observed result: RF-2005 ($42), RF-1009 ($14), RF-2032 ($49), RF-1015 ($27), RF-1026 ($12), RF-2072 ($17.72), RF-1046 ($26), RF-2119 ($48), RF-1055 ($23), RF-2171 ($11.86); count 10; total amount_usd $270.58. No post-decision fields were supplied and no paid IDs were inferred from history.
- Executed checks: valid policy parsing preserved a numeric amount operand as `Decimal`; a matching row was returned as a list; a valid nonmatching policy returned `[]`; changing a custom amount limit from 7.00 to 8.00 changed the result; unreadable, malformed, invalid-schema, and unsupported-operator policy inputs raised `PolicyInputError`; an invalid policy was rejected before request-row iteration; all five post-decision fields did not affect eligibility and were absent from results; source row mappings were unchanged; already-paid IDs and all occurrences of repeated candidate IDs were excluded.
- Remaining limitations: no UI or complete manager workflow; no activation, live pointer, payments, source-data writes, or integration; policy correctness and future-case safety are not established. The task remains `in-progress` for the full manager mechanism and independent review.

### Synthetic policy test fixtures — 2026-11-02

Changed artifacts: `tests/fixtures/policies/empty_allow.json`, `changed_mind_narrow.json`, `allow_or_all_of_and.json`, and `unsupported_operator.json`; `tests/test_policy_preview.py`; `tests/test_policy_evaluator.py`. Every fixture has `status: test` and explicitly says it is synthetic and not approved policy. The narrow changed_mind fixture uses a test-only $8 cap and supported evaluator conditions; the two-rule fixture uses synthetic reason values to test allow-list OR and condition AND. The unsupported-operator fixture is valid JSON and rejected by the closed schema. No production/example/template policy was changed, and these fixtures make no real-world policy recommendation.

Exact focused command from the repository root: `.\.venv\Scripts\python.exe -m unittest tests.test_policy_preview tests.test_policy_evaluator` — 20 tests passed on 2026-11-02. This is local test evidence only; it does not establish production readiness or policy acceptance.

Additional synthetic fixtures: `changed_mind_delivery_window.json`, `tenure_floor_customer_unpaid.json`, and `blank_notes_fraud_clear.json`. Each has `status: test` and explicitly says it is synthetic and not approved policy; all thresholds are test values only. `tests/test_policy_preview.py` uses in-memory rows to verify a match and nearby non-matches for each fixture, including both delivery-day bounds with amount/fraud, the tenure floor with customer/already-paid checks, and blank customer/support notes with fraud state. No dataset replay was run and no source, example, or template policy was changed.

Exact focused command from the repository root: `.\.venv\Scripts\python.exe -m unittest tests.test_policy_preview tests.test_policy_evaluator` — 23 tests passed on 2026-11-02.

### Focused runtime revalidation — 2026-11-02

Owner: Project QA
Scope: runtime revalidation only; no code or fixture changes.

- Exact Windows PowerShell command from the repository root: `& .\.venv\Scripts\python.exe -m unittest tests.test_policy_preview tests.test_policy_evaluator`
- Result: 23 tests passed (`Ran 23 tests in 0.034s`, `OK`).
- This confirms the focused local preview/evaluator test suite runs in the checked-in virtual environment. It does not establish policy acceptance, production readiness, or completion of the full manager mechanism. Task remains `in-progress`.

### Independent QA revalidation — 2026-11-02

Owner: Project QA
Status: in-progress
Scope: read-only review of the four synthetic policy fixtures and the focused preview/evaluator tests; no code or fixture changes.

- All four fixtures have `status: test` and explicitly identify themselves as synthetic and not approved policy.
- `empty_allow.json` is checked by preview and evaluator tests to return no matches / route to review.
- `changed_mind_narrow.json` is checked with a matching row, a $8.01 non-match against its test-only $8 cap, and changed $7/$8 policy values that change eligibility. Its supported reason, delivery window, amount, tenure, fraud, customer/note, and already-paid conditions are represented in the fixture.
- `allow_or_all_of_and.json` is checked with one matching row per rule. The negative alpha amount/fraud cases and beta amount/support-note cases establish that each rule's conditions must all pass, while either rule can match.
- `unsupported_operator.json` contains valid JSON with `amount_usd_gte`; preview parsing and evaluator validation both assert fail-closed rejection.

Executed from the repository root with the checked-in virtual environment on 2026-11-02: `.\.venv\Scripts\python.exe -m unittest tests.test_policy_preview tests.test_policy_evaluator` — 20 tests passed. This independently confirms the recorded fixture/test intent and local behavior only; it does not establish policy acceptance, production readiness, or activation readiness. Task remains `in-progress`.

### Explicit boundaries

This is a deterministic preview utility, **not** the complete manager mechanism. No UI, LLM/provider calls, policy editing/versioning, live/draft comparison, activation, rollback, live-file pointer, reviewer queue, payment calls, source-data writes, or integration into `main.py`. It previews supplied rows against supplied policy data; it does not authorize refunds or establish that future cases are safe. Keep this task `in-progress` until the acceptance checks pass and evidence is recorded here.

## Superseded full-manager design (reference only; not implementation scope)

The material below is retained as historical design context only. Its UI, LLM drafting, versioning, live-pointer, activation, rollback, and broader replay requirements are not part of this bounded task and are not authorized by this handoff.

The full manager workflow and reviewer page remain deferred and are not part of this bounded utility.

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

For the initial policy draft, the same boundary can accept source context through a function rather than file uploads:

```python
draft_policy_from_notes(data_dictionary: dict[str, str], raw_policy_text: str) -> dict
```

`data_dictionary` contains field names and meanings as structured data. `raw_policy_text` contains the policy notes rendered as text from the source CSV; do not pass customer rows to this function. The prompt also supplies the closed policy schema and allowed operators. The result is a **draft response**, for example `{ "status": "draft", "policy": {...}, "assumptions": [...], "cannot_express": [...] }`; it is not the active policy file. Deterministic schema validation must reject unsupported fields/operators, and an operator must review the assumptions and preview the draft before any explicit activation. If the source text cannot be expressed safely in the schema, return `cannot_express` rather than inventing a rule. The LLM does not evaluate requests, write the live pointer, or authorize refunds.

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

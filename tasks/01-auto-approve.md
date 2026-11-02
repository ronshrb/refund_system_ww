# 01 — What can be auto-approved

Owner: project-product-manager  
Status: in-progress  
Dependencies: task 00  
Business outcome: Pay a refund with no person only when every structured check is a sure yes, and leave the rest with a reviewer.  
Acceptance checks:
- The evaluator is data-driven: supported thresholds and conditions come from the supplied policy JSON, not hardcoded business values. Malformed policy and unsupported operators fail closed.
- Non-finite Decimal amount operands in direct policy mappings are rejected as invalid policy; NaN and positive/negative Infinity must never raise or approve.
- An offline, policy-only replay on history passes the golden set: `{ "allow": [] }` yields zero auto-approvals; `policy/auto-approve.example.json` yields exactly the 10 request IDs recorded below, with no extras or duplicate approvals. Executed locally; see evidence below.
- Evaluator request inputs are projected from history/pending without `decision`, `decided_by`, `outcome_note`, `decided_at`, or `review_minutes`. Focused tests establish that these fields cannot affect policy eligibility.
- Focused fail-closed checks show that missing/invalid inputs, unknown rules, fraud yes/unknown, nonblank notes, and an explicitly supplied already-paid request ID cannot auto-approve. Keep idempotency context separate from evaluator request fields. Executed locally; see evidence below.
- Before any activation, shadow-evaluate the implementation, review every proposed auto case against intake-available evidence, and establish named owners, denominators, baselines, targets, and rollback thresholds for post-launch monitoring. No activation or real payment is in scope here.
- Record the evidence, exceptions, and remaining limitations below. Keep this task in-progress for remaining shadow-review and activation-readiness gates; production activation additionally requires policy acceptance and operational readiness.

## Proposed, not activated

This line is a proposal. It does not activate policy and it does not move money.

Task 00 left the tenure cutoff open. The floor and the cap below are proposed. The user has not accepted either number.

`decided_by=auto` is observed behavior. It is not this list. Task 00 already marks some of those autos unsafe: blank tenure, `never_arrived`, messy fraud flags.

This list is the **proposed contents of policy v1**, not a prompt. Auto-approve is a function over that JSON (task 02). An LLM does not read this list and decide. It does not update the live file. It does not pay.

## Clarified implementation gate

**Recommendation to the Project Delivery Lead: proceed with bounded implementation of a generic deterministic evaluator and offline simulation.** The user's clarification authorizes testing the evaluator against supplied policy JSON without accepting the example's 3-month tenure floor or $49 cap as production policy. Those values remain proposed data in `policy/auto-approve.example.json`; they must not be copied into evaluator code or treated as live settings.

The evaluator may be generic over its documented, closed policy schema and supported operators. “Whatever policy JSON is supplied” means any valid policy within that schema; malformed JSON, unknown keys/operators, unsupported actions, or invalid values must fail closed to review. The offline simulator may read history and pending request data, but its evaluator request projection excludes all five named post-decision fields: `decision`, `decided_by`, `outcome_note`, `decided_at`, and `review_minutes`.

Historical replay and idempotency are separate:

- The 10-ID history golden-set replay is policy-only. It does not pass historical decisions or use them to determine eligibility. This keeps the expected cohort reproducible and avoids treating old decisions as labels.
- Idempotency is a separate input/context, such as an explicit set of request IDs already paid or conservatively treated as resolved. Test it independently by showing a matching ID routes to review. Do not infer this context from a candidate row's own historical decision.
- If historical `decision` is used to seed idempotency context for a pending-data simulation, use it only to add request IDs with `approved` or `partial` to a separate “potentially resolved” exclusion set. It must not be passed as a request field, change any rule result, or make another request more eligible. The sample is not a payment ledger, so this is a conservative duplicate-risk proxy, not proof that money moved; disclose this in simulation output. Do not use `decided_by`, `outcome_note`, `decided_at`, or `review_minutes` for this purpose.

This authorization does **not** authorize activation, publishing a live policy, real payment calls, or claims that the proposed thresholds are accepted or safe for production. The reviewer remains the destination for every non-match and uncertain case.

## How auto-approve runs

Same request + same live JSON → same result, every time. That is the consistency. A chat agent will not give you that.

```text
load live policy JSON
evaluate(request, policy) → approve_full | review, plus which rule/rail fired
if approve_full: record decided_by=auto
else: queue for a person
```

`evaluate` is ordinary code: rails first, then this allow-list, fail closed. It is the only thing that may set auto. If you have an agent at intake, it may **call** that function (a tool). It may not interpret the JSON itself, and it has no `approve()` tool.

The manager talking to an agent (task 02) is a different path. That agent may draft a JSON patch. It cannot run this list, and it cannot move the live pointer.

## The evaluate tool

One function. It is also the only tool intake may call. It does not read `comment` or `source_note`. It does not invent operators. Unknown key, bad JSON, or a field that will not parse → `review`. Never `approve_full` on those.

```text
evaluate(request, policy) →
  { decision: approve_full | review,
    policy_version,
    matched_rule: id or null,
    failed_checks: [ { rule_id, group, operator, got } ] }
```

**Walk.** For each object in `policy.allow`, in order: every entry in `all_of` must pass, and every key in that entry’s `when` must pass. If they all pass and `action` is `approve_full`, stop and return that. If no rule passes, `review`. Two `allow` objects would be OR (first hit wins). This file has one, so notes 1 and 2 cannot pay alone.

**Closed operators.** Anything not in this table is unknown → `review` for the whole request.

| JSON key | Passes when the parsed request… |
|---|---|
| `stated_reason` | equals the string (after the task 00 clean: `changed mind` → `changed_mind`) |
| `days_since_delivery_gte` / `_lte` | integer, in range. Blank or negative fails the gte/lte as written |
| `amount_usd_gt` / `_lte` | number, in range. Parse `$`, commas, `USD`, spaces first. Unparseable fails |
| `tenure_months_gte` | integer ≥ N. Blank fails |
| `fraud_flag` | `"no"` means cleaned `no` / `N` / `false`. `yes`, `Y`, blank fail |
| `customer_id_present` | true → id non-empty |
| `customer_note_blank` | true → missing or whitespace only. Do not read the text |
| `support_note_blank` | true → missing or whitespace only. Do not read the text |
| `request_id_not_already_paid` | true → this id has no prior `approved` / `partial` |

`simulate(policy)` is the same function over `history.csv` and pending, used by the manager preview. No second implementation.

Not a tool: `approve()`, writing live JSON, or an LLM “checking” the file.

## Allow-list

Approve the full parsed `amount_usd`, with no person, only when every item below is true.

1. `stated_reason` cleans to `changed_mind`. The raw value `changed mind` is the same reason.
2. `days_since_delivery` parses to an integer from 0 through 14 inclusive. Blank or negative fails.
3. `fraud_flag` cleans to `no` (`no`, `N`, `false`). `yes`, `Y`, and blank fail. Task 00 already maps `N` and `false` to no. None of the 10 matches used those tokens.
4. `tenure_months` parses to an integer of 3 or more. Proposed floor. Blank fails.
5. `amount_usd` parses to a number above 0 and at most $49. Proposed cap. Parse strips `$`, commas, `USD`, and spaces, as in task 00.
6. `customer_id` is present.
7. `customer_note` is blank. This line does not read that field and does not follow instructions in it.
8. `support_note` is blank. This line does not read that field.
9. This `request_id` has not already been paid. A second copy is not a second refund.

`prior_refunds_12m` is not a condition. It cannot add trust and it cannot push a case off this list.

## Example policy JSON (auto-approve only)

File: `policy/auto-approve.example.json`. Proposed, not activated. This is the allow-list above as data. Rails and review rules are not in this file.

Notes 1 and 2 are **both required** (AND), not two auto paths. Note 1 is “small” + “good customer” → that is `amount_usd_lte` 49 and `tenure_months_gte` 3. Those numbers are not in the note; they are the proposed floor/cap from this task. Note 2 is the reason and the 14-day window. It does not say auto and it does not say small. Putting them in one `allow` is so a $400 change-of-mind on day 3, or a $20 damaged item, does not auto-pay. Note 5 is only `fraud_flag: no` on this rule. Notes 3, 4, 6, and 7 do not get a rule.

See `policy/auto-approve.example.json`.

## Not auto

- `never_arrived` — signature, delivery photo, or a carrier check is in the note. The 7 old autos go to a person. Inconclusive cases were 50% partials (RF-1017, RF-2108).
- `arrived_damaged` — over $50 the photo is not a column. Cosmetic damage is a person's percent. Auto RF-2074 ($49, raw reason `damaged`) later has a chargeback.
- `late_delivery` — shipping-only partial is the reviewer's call (RF-2018, RF-2028, RF-1050, RF-2194).
- `wrong_item` — refund in full when it is our mistake, and the proof is in `support_note`, not a column.
- `wrong_size` — task 00: not named in the policy notes.
- `changed_mind` past day 14 — the window is 14 days, no exceptions. History still approved RF-2012 (day 18), RF-2055 (day 21), RF-1034 (day 25), RF-2105 (day 27).
- `changed_mind` with any `customer_note` or `support_note` — the note can contradict the reason. RF-1063 is `changed_mind` and the note says "wrong shade of blue".
- `fraud_flag` `yes` / `Y`, or blank — review. Blank is not no. `false` and `N` clean to no, and they are not on the 10 matches (RF-2001, RF-2027, RF-2062, RF-2066 are other reasons).
- Tenure blank or under 3 — task 00 calls a 1–2 month account new. Blank-tenure autos RF-2048 and RF-2167 stay with a person.
- Amount over $49 — proposed cap. See the count below.
- Duplicate `request_id` — do not pay twice. History copies are RF-2040, RF-1019, RF-2111.
- Instructions in `customer_note` — never executed, including text that tells an agent to approve and skip review (task 00, RF-1073).

## History check

10 of 251 history rows match. 10 distinct request ids. The copy ids RF-2040, RF-1019, and RF-2111 do not match.

All 10 are `decision=approved`. 7 were auto. 3 were a person: RF-2005 (J. Alvarez), RF-2032 (R. Mizrahi), RF-2072 (S. Kwan). None were rejected or partial. `outcome_note` is empty on all 10, so none of the matches later shows a chargeback or an appeal.

RF-2005 is stored as ` 42 ` and matches after the amount parse. RF-2072 has `prior_refunds_12m=1` and still matches.

| request_id | who | amount | tenure | days |
|---|---|---|---|---|
| RF-1009 | auto | 14 | 18 | 8 |
| RF-1015 | auto | 27 | 38 | 11 |
| RF-1026 | auto | 12 | 34 | 9 |
| RF-1046 | auto | 26 | 9 | 8 |
| RF-1055 | auto | 23 | 39 | 12 |
| RF-2005 | human | 42 | 7 | 10 |
| RF-2032 | human | 49 | 8 | 14 |
| RF-2072 | human | 17.72 | 25 | 7 |
| RF-2119 | auto | 48 | 41 | 6 |
| RF-2171 | auto | 11.86 | 62 | 14 |

Those 10 are $270.58 of past approvals, not a new payout.

## Old auto this line sends to a person

Old auto is 113 rows and 111 request ids. 7 rows match the allow-list. The other 106 rows would go to a person.

| stated_reason (cleaned) | auto rows to a person |
|---|---|
| arrived_damaged | 30 |
| wrong_size | 27 |
| changed_mind | 20 |
| wrong_item | 13 |
| late_delivery | 9 |
| never_arrived | 7 |

106 rows are 104 ids. RF-2040 is stored twice under `late_delivery`. RF-1019 is stored twice under `wrong_size`. Four of the 106 used a raw alias (`changed mind`, `damaged`, `late`, `wrong item`), folded as in task 00.

Of the 20 `changed_mind` autos, 18 are inside 14 days, `fraud_flag=no`, tenure at least 3, and amount at most $49. They drop only because a note is filled. The other two are RF-2016 ($53) and RF-2044 ($54). Each also has `support_note` = "within window".

## Proposed floor and cap

Tenure at least 3. Task 00 says a 1–2 month account is new, blank tenure is unknown, and no cutoff is accepted yet. In-window `changed_mind` has one row under 3 months: RF-2078, tenure 1, $23.77, day 11, both notes blank, human approved, empty `outcome_note`. The floor sends that row to a person. There is no tenure-2 row and no blank tenure on in-window `changed_mind`. The 10 matches all have tenure 7 or more, so a floor of 4, 5, 6, or 7 would still be those same 10. The proposal stays at 3, the first month task 00 does not call new.

Amount at most $49. All 10 matches are at or under $49. The highest is RF-2032. Old auto never went above $54; that ceiling is observed, and this line does not adopt it. RF-2016 ($53) and RF-2044 ($54) would still be out at a $54 cap because each has a support note. The only in-window `changed_mind` above $54 is RF-1020 at $71 (human, day 14, tenure 4, note filled).

## Gate evidence and decision

The latest clarification expressly separates evaluator implementation from policy acceptance: implement thresholds as policy data and use the proposed example policy for offline simulation, without treating 3 months or $49 as accepted operating limits. The bounded evaluator/simulation scope above is implemented and its focused runtime checks passed. The example policy remains `proposed`; neither numeric value is accepted or activated.

Independent read-only recomputation from `data/raw/history.csv` applied the stated reason, delivery-day, fraud, tenure, amount, identity, and blank-note gates. It found 10 rows and 10 unique IDs: RF-1009, RF-1015, RF-1026, RF-1046, RF-1055, RF-2005, RF-2032, RF-2072, RF-2119, RF-2171. All 10 were historically approved; 7 were marked auto and 3 were decided by a person. Their recorded amount totals $270.58. None has a nonblank `outcome_note`. The three duplicated history request IDs (RF-2040, RF-1019, RF-2111) are not matches. This independently supports the documented cohort count, not the correctness or safety of the proposed line.

### Implementation evidence / results

Changed artifacts: `utils/policy_evaluator.py` adds the closed-schema, data-driven evaluator and request projection, and now rejects non-finite Decimal amount thresholds during policy validation; `main.py` adds a no-UI offline CSV runner; `tests/test_policy_evaluator.py` adds evaluator, isolation, fail-closed, Decimal, idempotency, and duplicate-ID checks. No source CSV or policy JSON was changed.

Executed commands from the repository root (Windows virtual environment):

- `.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator` — 9 tests passed.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests` — 17 tests passed.
- `.\.venv\Scripts\python.exe main.py --policy-json '{"allow":[]}'` — 251 input rows; 0 auto rows; 251 review rows.
- `.\.venv\Scripts\python.exe main.py --policy policy/auto-approve.example.json` — 251 input rows; 10 auto IDs; 241 review rows; policy status `proposed`; `simulation_only=true`; `payments_called=false`.

The example replay emitted exactly these distinct IDs and no others: RF-2005, RF-1009, RF-2032, RF-1015, RF-1026, RF-2072, RF-1046, RF-2119, RF-1055, RF-2171. Duplicated history IDs RF-1019, RF-2040, and RF-2111 were routed to review. The replay did not seed idempotency from historical decisions. The isolated idempotency test supplied an already-paid ID separately and confirmed review; a duplicate-input test confirmed every occurrence is excluded from auto IDs.

### Independent QA defect repair — 2026-11-02

Changed only `_valid_operand` in `utils/policy_evaluator.py`: amount thresholds supplied as `Decimal` must now satisfy `is_finite()`. `Decimal('NaN')`, `Decimal('Infinity')`, and `Decimal('-Infinity')` are rejected while parsing policy, and `evaluate` returns `review` with reason `invalid_policy`. No QA-owned test was edited. The regression test initially reproduced QA's reported 2 failures and 1 error; after the repair it passes.

Executed from the repository root with the checked-in virtual environment:

- ` .\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator` — 10 tests passed.
- ` .\.venv\Scripts\python.exe -m unittest discover -s tests` — 18 tests passed.
- ` .\.venv\Scripts\python.exe main.py --policy-json '{"allow":[]}'` — exit 0; 251 rows, 0 auto, 251 review; duplicate IDs RF-1019, RF-2040, and RF-2111 routed to review.
- ` .\.venv\Scripts\python.exe main.py --policy policy/auto-approve.example.json` — exit 0; 251 rows, 10 auto, 241 review; exact IDs RF-2005, RF-1009, RF-2032, RF-1015, RF-1026, RF-2072, RF-1046, RF-2119, RF-1055, RF-2171; `simulation_only=true`, `payments_called=false`.

These are local evaluator and history-fixture checks only. Independent QA revalidation remains the next handoff; shadow review and operational readiness gates remain outstanding.

Limitations and unexecuted gates: this is an offline simulation, not a payment or activation path. `pending.csv` simulation was not run in this slice. No proposed case has received independent shadow review, and named monitoring owners, denominators, baselines, targets, outcome-maturation window, and rollback thresholds are not established. The sample remains only 251 historical rows; past decisions are not ground-truth labels, and missing outcome notes do not prove absence of later harm. It does not establish that $49 is the right definition of “small” or that 3 months defines a “good customer”; all 10 matches have tenure at least 7 months. The 3-month floor and $49 cap remain explicitly unaccepted proposals.

The user supplied the full assignment text, now summarized in `docs/assignment-brief.md`. The PDF remains the original source of truth, but the supplied text is sufficient for the requirements relevant to this task; no PDF-only requirement is being assumed.

## Stop, revise, and rollback

- **Implementation gate:** the bounded generic evaluator and offline history simulation are implemented and the focused runtime checks pass. Do not hardcode the example values. Keep this task in-progress until shadow review and remaining operational readiness gates are resolved.
- **Stop before activation:** policy owner has not explicitly accepted/replaced the numeric values and full line; golden-set, field-isolation, and fail-closed checks are not passing; proposed auto cases have not been reviewed against intake-available evidence; or named monitoring owners, denominators, baselines, targets, outcome-maturation window, and rollback thresholds are not set. Never interpret a shadow approval as a real refund.
- **Revise the line:** explicit policy interpretation changes; reviewer adjudication finds any proposed auto case ineligible on information available at intake; a valid edge case falls outside the accepted input contract; or later customer/financial outcomes show harm. Re-run the cohort and focused tests after each policy revision. Do not use historical decisions alone as correctness evidence.
- **Pause/rollback after activation:** any evaluator/policy mismatch, approval on malformed or unknown input, duplicate payment, wrong amount, or approval of a case outside the accepted line triggers immediate disablement of auto-approval and review of affected cases. Monitor auto-approval count / total requests, auto-approved dollars, and adverse outcomes / auto-approvals with mature outcomes. Baselines, target thresholds, outcome-maturation window, and named operational owner are not established; set them before any live activation. Any material adverse-outcome signal against the agreed threshold also triggers pause and review.

## Next dependency

The bounded evaluator and history replay checks are complete. Activation remains blocked on explicit policy-owner acceptance or replacement of both numeric values, review of the proposed cases, and operational monitoring/rollback readiness. Pending-data simulation and broader shadow evaluation remain future work; the example-policy replay is a simulation, not activation.

## Independent QA pass — 2026-11-02

Owner: Project QA  
Status: in-progress  
Scope: independent Part 1 evaluator/runner validation. No application code was changed. Added one focused regression test in `tests/test_policy_evaluator.py`.

### Finding for Engineering

**Blocker — non-finite Decimal policy operands are not rejected.** `_valid_operand` accepts `Decimal` thresholds without checking `is_finite()`. A direct mapping with `amount_usd_lte: Decimal("Infinity")` approves the otherwise eligible $49 request. `Decimal("NaN")` raises `decimal.InvalidOperation` during evaluation instead of returning review, and `Decimal("-Infinity")` is accepted and reported as `no_allow_rule_matched` rather than invalid policy. Reject every non-finite Decimal amount operand while parsing policy, and preserve fail-closed review behavior. Regression test: `test_non_finite_decimal_policy_operands_fail_closed`.

### Checks that passed

- Post-decision isolation: `project_request` removes `decision`, `decided_by`, `outcome_note`, `decided_at`, and `review_minutes`; including those fields in the raw request did not change eligibility under the supported policy. Supported operators only read their fixed intake fields.
- Duplicate IDs: runner routes every occurrence of an ID duplicated in one input file to review; focused runner test produced zero approvals for two otherwise eligible copies. Both golden replays routed RF-1019, RF-2040, and RF-2111 to review.
- Empty policy golden: 251 rows, 0 auto, 251 review.
- Example policy golden: 251 rows, 10 auto IDs (RF-2005, RF-1009, RF-2032, RF-1015, RF-1026, RF-2072, RF-1046, RF-2119, RF-1055, RF-2171), 241 review; `simulation_only=true`, `payments_called=false`.

### Exact verification

All commands ran from the repository root with the checked-in virtual environment on 2026-11-02.

- Baseline `.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator` — 9 tests passed before adding the regression.
- Baseline `.\.venv\Scripts\python.exe -m unittest discover -s tests` — 17 tests passed before adding the regression.
- `.\.venv\Scripts\python.exe main.py --policy-json '{"allow":[]}'` — exit 0; 251 rows, 0 auto, 251 review.
- `.\.venv\Scripts\python.exe main.py --policy policy/auto-approve.example.json` — exit 0; 251 rows, 10 auto, 241 review, exact IDs listed above.
- `.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator` after adding the regression — 10 tests run; 2 failures and 1 error, all in the non-finite-operand subcases (Infinity approves, -Infinity is not rejected as invalid policy, NaN raises `InvalidOperation`).
- `.\.venv\Scripts\python.exe -m unittest discover -s tests` after adding the regression — 18 tests run; the same 2 failures and 1 error.

### Limits and handoff

This verifies the local deterministic evaluator and the supplied history fixture only. It does not establish policy correctness, production safety, pending-data behavior, payment idempotency against a real ledger, or activation readiness. Do not repair or activate in QA; route the non-finite operand defect and regression to Engineering, then rerun the failing focused test, full suite, and both golden commands after repair. Keep this task `in-progress` until that revalidation and the existing shadow-review/operational gates are complete.

## Decision options and assumptions

- **Recommended:** implement the schema-bounded, data-driven evaluator and offline policy replay now. This tests deterministic behavior and the proposed JSON without endorsing its thresholds.
- **More conservative alternative:** defer all evaluator work until the policy owner accepts both values. This avoids testing a draft policy but unnecessarily couples reusable evaluator mechanics to business approval.
- **Not recommended:** hardcode 3 and $49, or treat the 10 historical matches as proof of policy correctness. That conflicts with the clarification and overstates what the sample shows.

Assumptions: the example JSON defines the supported policy-schema baseline for this demo; policy values are reviewed as data; “already paid” is not directly observable in these CSVs. If history decisions seed a pending simulation's idempotency context, `approved` and `partial` mean only “potentially resolved; conservatively exclude,” not verified payment. A production-quality idempotency source is not present.

Failure modes to test or disclose: malformed/unsupported policy accidentally approving; missing or invalid request fields silently passing; any of the five post-decision fields leaking into eligibility; candidate's own historical decision contaminating replay; false duplicate blocking from treating historical approvals as confirmed payments; repeated request IDs producing duplicate simulated approvals; or simulation results being mistaken for live authorization. Unknowns and ambiguity route to review.

## Independent QA revalidation — 2026-11-02

**Decision: pass for the standalone evaluator slice only; task remains in-progress.** The current `main.py` is empty and contains no evaluator reference. `utils/policy_evaluator.py` performs no file I/O and does not read source CSVs or policy files; policy and request data are supplied by the caller. No application integration, CSV replay, policy activation, or payment path was validated or performed.

The focused tests cover all supported operators: `stated_reason`, both delivery-day comparisons, both amount comparisons, tenure, fraud, customer ID presence, blank customer/support notes, and the separately supplied already-paid ID condition. Tests vary policy operands rather than asserting the proposed business thresholds as evaluator constants. Malformed policy, unsupported action/operator/value, and invalid request inputs route to review.

Exact command from the repository root: `.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator` — **11 tests passed, 0 failures** (2026-11-02).

Earlier implementation and golden-replay claims in this task, including `main.py` runner behavior and history CSV results, are **previous-snapshot evidence, not current validation**: the corresponding runner is absent from the current `main.py`, and replay commands were not run in this QA pass. No application or test files were changed.

## What would move this line

- The user accepts or replaces the tenure floor. Task 00 did not accept one.
- The user accepts or replaces the $49 cap. A cap of $54 adds no history row while notes must stay blank.
- Allowing a `support_note` of only "within window", with the cap still $49, adds 5 rows: RF-1003, RF-2064, RF-2122, RF-2132, RF-2154. All 5 were auto, approved, with an empty `outcome_note`. This line does not read that phrase.
- Allowing customer notes would also take contradictions: RF-1063 ("wrong shade of blue") and RF-2014, RF-2103, RF-2144 ("colour looks different in person").
- A real column for a photo, a mis-pick, or a signature could add another reason later. Not this list.

## Remaining limitations

Counts are `history.csv` only: 251 rows, about Jun–Aug 2026. A past approval is not proof the next case is safe. This file does not authorize a refund.

`wrong_size` stays off until someone explicitly chooses to add it: the same gates would be a wider set, and task 00 does not name that reason.

## Bounded evaluator implementation - 2026-11-02

Status: `in-progress`. This entry records only the authorized standalone evaluator slice; it does not mark the policy task complete or establish policy acceptance or production safety.

Changed artifacts: `utils/policy_evaluator.py` implements `evaluate(request, policy, *, already_paid_ids=None)`; `tests/test_policy_evaluator.py` contains in-memory contract tests. Policy thresholds and conditions are read from the validated policy mapping; the proposed example's `$49` and 3-month values are not hardcoded. The evaluator is not wired into `main.py` or another application path. The already-paid ID set is separate context, and its absence routes a policy using that operator to review.

Supported operators: `stated_reason`, `days_since_delivery_gte`, `days_since_delivery_lte`, `amount_usd_gt`, `amount_usd_lte`, `tenure_months_gte`, `fraud_flag`, `customer_id_present`, `customer_note_blank`, `support_note_blank`, and `request_id_not_already_paid`. `allow` rules are ordered OR; `all_of` entries and conditions within each `when` mapping are AND. Unsupported or malformed policy, unknown operators, malformed relevant inputs, unknown fraud state, and non-finite numeric policy amounts return review. Amounts compare as `Decimal`. Results contain `decision`, `reason`, `matched_rule`, and `policy_version`.

Executed from the repository root on 2026-11-02: `.\.venv\Scripts\python.exe -m unittest tests.test_policy_evaluator` - 11 tests passed. Coverage includes each listed operator, custom policy values, OR/AND behavior, exact amount boundaries and currency parsing, malformed policies, missing/invalid inputs, unknown fraud, note handling, non-finite Decimal operands, and separate already-paid context.

Not executed in this slice: history or pending replay, the full test suite, application/CSV runner, shadow review, or activation-readiness checks. No application file, source CSV, or policy JSON was changed. The proposed example policy remains unaccepted and inactive.

Next dependency: independently review this evaluator contract, then obtain separate authorization for any application integration and request projection. Policy-owner acceptance, shadow review, and operational monitoring/rollback readiness remain required before any activation.

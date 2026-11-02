# 01 — What can be auto-approved

Owner: project-product-manager  
Status: in-progress  
Dependencies: task 00  
Business outcome: Pay a refund with no person only when every structured check is a sure yes, and leave the rest with a reviewer.  
Acceptance checks: not set yet.

## Proposed, not activated

This line is a proposal. It does not activate policy and it does not move money.

Task 00 left the tenure cutoff open. The floor and the cap below are proposed. The user has not accepted either number.

`decided_by=auto` is observed behavior. It is not this list. Task 00 already marks some of those autos unsafe: blank tenure, `never_arrived`, messy fraud flags.

This list is the **proposed contents of policy v1**, not a prompt. Auto-approve is a function over that JSON (task 02). An LLM does not read this list and decide. It does not update the live file. It does not pay.

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

## What would move this line

- The user accepts or replaces the tenure floor. Task 00 did not accept one.
- The user accepts or replaces the $49 cap. A cap of $54 adds no history row while notes must stay blank.
- Allowing a `support_note` of only "within window", with the cap still $49, adds 5 rows: RF-1003, RF-2064, RF-2122, RF-2132, RF-2154. All 5 were auto, approved, with an empty `outcome_note`. This line does not read that phrase.
- Allowing customer notes would also take contradictions: RF-1063 ("wrong shade of blue") and RF-2014, RF-2103, RF-2144 ("colour looks different in person").
- A real column for a photo, a mis-pick, or a signature could add another reason later. Not this list.

## Remaining limitations

Counts are `history.csv` only: 251 rows, about Jun–Aug 2026. A past approval is not proof the next case is safe. This file does not authorize a refund.

**Lead decision:** accept this allow-list as the proposed line. Tenure floor and $49 cap stay proposed. `wrong_size` stays off until someone chooses to add it: the same gates would be a wider set, and task 00 does not name that reason.

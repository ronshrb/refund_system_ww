# 00 — Understand the data

Owner: project-product-manager  
Status: in-progress  
Dependencies: none  
Business outcome: Know what the sample columns can support, and what we should clean before we use them.  
Acceptance checks:
- A read-only normalization run writes `data/processed/history.csv`; `data/raw/history.csv` remains byte-for-byte unchanged.
- The processed copy retains the source columns, row order, and all 251 rows, including duplicate request rows; only `requested_at` and `amount_usd` are normalized.
- Supported dates normalize to `DD/MM/YYYY`; missing, impossible, or unsupported values return invalid rather than a guessed date. Invalid rows must go to review, never approval.
- Supported amounts normalize to exact `Decimal` numbers with two fractional digits and no currency marker or grouping comma. Missing, negative, malformed, or unsupported values return invalid and go to review.
- Focused parser checks cover each accepted format, `12/06/2026`, malformed dates, currency-prefix/suffix/grouping examples, excess decimal precision, and invalid amounts.

## Evidence / results

### Why `prior_refunds_12m` can't help us

The column is refunds **granted** in the last 12 months, not requests, and not a refund rate.

We have no orders or items-bought field. So 3 refunds could be 3 of 3 orders or 3 of 80. High can mean a known customer we have refunded before, or something odd. Low can mean a new account, or a long-time buyer who never needed a refund. Same number, two stories.

Without that denominator we cannot score trust or “needs review” from this field. Do not add trust points for a high prior. Do not send someone to review only because the prior is high.

### Why and how `tenure_months` can

Tenure is months since the account was created. It does not need a purchase count. A 1–2 month account is new; a blank tenure is unknown. That is enough to say we do not know this customer yet.

How to use it: if tenure is very low or blank, put a review flag on the request. A person looks. It does not reject and it does not approve. Longer tenure does not by itself mean auto-approve; other fields still decide that.

No cutoff accepted yet.

### `customer_id` — past rejection as a review flag

Idea: if this `customer_id` is in history and a past request was rejected, flag the new one for review.

It does not help in this sample. 7 IDs have more than one history row; 3 of those are the same request copied twice. The real repeats (CU-1044, CU-1733, CU-1217, CU-1388) were all approved. No customer has a rejection and then another request. The pending overlaps (CU-1044, CU-1217, CU-2894) only have past approvals, so the flag would not fire.

A past reject is also a weak label. Some were overturned on appeal (`outcome_note` on RF-1008, RF-2090, RF-2143). Some are just “outside 14 days.” That does not mean the next request is untrustworthy.

What `customer_id` can do: join to history. Blank id cannot join (RF-2065). Being in history with approvals is not trust.

What else it can do (examples; RF-2183 and RF-1077 are in `pending.csv`, not `history.csv`):

- **Reviewer context (for the UI later).** When a reviewer opens a request, join on `customer_id` and show that customer's other tickets from history: date, amount, reason, decision, decided by, and `outcome_note` (appeal / chargeback). The person reads them and decides. We do not turn past approvals or rejects into points. Blank `customer_id` → nothing to show.
  Example: pending RF-1068 (CU-1044) should show history RF-1002, RF-1010, RF-1018, RF-1028, RF-1039 (five approvals, amounts $45 → $340).
- **Resubmit / duplicate.** Same customer, same or near request. Treat as one case, not two.
  Example (`pending.csv`): RF-2183 twice for CU-2418 — 2026-09-03 $49 `wrong_item`, then 2026-09-04 $52 with note “resubmitting - forgot to include the shipping cost.”
  Example (`history.csv`): exact copied rows RF-2040, RF-1019, RF-2111.
- **Broken identity.** Same id, impossible tenure. The row is untrustworthy → review.
  Example: CU-2894 is tenure 19 on `history.csv` RF-2178 (2026-08-18, $30.45, auto-approved) and tenure 1 on `pending.csv` RF-1077 (2026-08-31, $260, `fraud_flag=Y`, note says new account / first order). Tenure cannot drop 19 → 1 in 13 days.

### Appeals / `outcome_note`

Do not preprocess history to overwrite `decision`. The CSVs stay as recorded. `decision` is what was decided then; `outcome_note` is what happened after (appeal, chargeback). Flattening them hides that the first call was wrong.

Overturning is usable as a **later fact**, not as intake:

- Derived flag for analysis only (e.g. rejected-then-refunded). Use that so we do not treat RF-1008, RF-2090, RF-2143 as “correct denies.”
- Show it on the customer's past tickets. RF-2143 was our mistake (mis-pick); policy already says refund in full.
- Do not use an appeal to auto-trust or auto-approve the next request. We will not have `outcome_note` at intake on a new case.

### `decision` = `partial`

9 / 251 history rows. Always a human, never auto. Not only late delivery.

| Reason | n | What they paid |
|---|---|---|
| `late_delivery` | 4 | shipping refunded only (RF-2018, RF-2028, RF-1050, RF-2194) |
| `arrived_damaged` | 3 | 25–30% (RF-2079, RF-1060, RF-2162) — cosmetic |
| `never_arrived` | 2 | 50% (RF-1017, RF-2108) — tracking inconclusive |

Policy note 6 names partials for cosmetic damage or late delivery as the reviewer's call. The two `never_arrived` 50% cases are observed, not in that note.

### `fraud_flag`

If the flag is yes → review. Do not auto-approve. Policy note 5: goes to the fraud team; stolen card can still be a full refund to the real cardholder.

The column is messy. Treat `yes` and `Y` as yes. History: `yes` 16, `Y` 4, `no` 226, `false` 3, `N` 1, blank 1 (RF-2134). Pending: `yes` 1 (RF-1070), `Y` 1 (RF-1077), blank 1 (RF-2182 — notes say stolen card / fraud team confirmed). Blank is not `no`.

### Columns to clean / fix (at parse time; do not rewrite the CSVs)

- **`fraud_flag`:** `yes`/`Y` → yes; `no`/`N`/`false` → no; blank → unknown (review).
- **`stated_reason`:** spaces and case (`changed mind`, `arrived damaged`, `wrong item`, `late`, `Never Arrived`) → the six dictionary values.
- **`category`:** `Home` / `Electronics` → lowercase.
- **`amount_usd`:** `$1,150.00`, `$43`, `USD 120.00`, `55.00 USD` → a number.
- **`requested_at`:** `03/09/2026`, `12/06/2026`, `2026-06-15T07:11:00Z` → a date.
- **Blank / broken:** `customer_id` (RF-2065), `tenure_months` (RF-2048, RF-2167), `days_since_delivery` (RF-2050). CU-2894 tenure 19 then 1.
- **Duplicate `request_id`:** history RF-2040, RF-1019, RF-2111 (exact copies); pending RF-2183 (resubmit, not a copy).

### Normalization decision for `requested_at` and `amount_usd`

Decision for the processed copy: keep the original column names and normalize only these two values. Write dates as day-first dates (`DD/MM/YYYY`, for example, `30/06/2026`) and amounts as exact `Decimal` numbers with two fractional digits (for example, `43.00`), without a currency marker. Use `Decimal`, not binary floating point, for money.

- **Dates accepted:** strict `YYYY-MM-DD`; strict `DD/MM/YYYY`; and UTC timestamps in the observed `YYYY-MM-DDTHH:MM:SSZ` form. Keep the UTC calendar date from a `Z` timestamp. Do not infer formats row by row. Adopt day-first for every slash date, so `12/06/2026` means 12 June 2026. Render every valid date as `DD/MM/YYYY`. A missing, impossible, or unsupported date is invalid; do not substitute a default or silently reinterpret it. The parser/evaluator must send an invalid row to review.
- **Day-first assumption:** task notes identify the slash examples as `DD/MM/YYYY`, but no locale/source contract confirms that convention. Day-first is the chosen demo convention and makes ambiguous source values deterministic. If the source convention is later found to differ, revise the parser and rerun the tests; do not guess per value.
- **Amounts accepted:** trim surrounding whitespace; accept unsigned digits with an optional decimal part of one or two digits and correctly grouped thousands commas, plus at most one optional USD marker (`$` prefix, or `USD` prefix/suffix). Parse the full string as a non-negative decimal. Reject missing values, negative signs, malformed grouping, unsupported currencies, more than two fractional digits, repeated/conflicting markers, or trailing text. Render valid values without grouping/currency markers, with two decimal places. An invalid amount is invalid input and must go to review, not approval.
- **Unsupported dates:** timezone-less timestamps, non-UTC offsets, and timestamp variants not present in the sample are not accepted in this narrow pass. Add them only with an explicit convention and tests.

### Normalization evidence / verification

Read-only CSV inventory of `data/raw/history.csv`: 251 rows; 243 ISO dates, 4 slash dates, and 4 UTC `Z` timestamps. The chosen day-first convention renders RF-2025 `09/06/2026` → `09/06/2026`; RF-2031 `12/06/2026` → `12/06/2026`; RF-2073 `02/07/2026` → `02/07/2026`; RF-2137 `03/08/2026` → `03/08/2026`. The existing sample inventory is not runtime verification of the updated implementation.

All 251 amount strings matched the bounded grammar and parsed as decimals. Eight contain non-plain formatting (whitespace or a currency marker), including `USD 120.00`, `$43`, `$36`, and `55.00 USD`. These are sample-inventory checks only: no processed copy was generated, no source-file checksum comparison was run, and the focused application/parser acceptance checks above remain unexecuted.

Limitations: day-first is a documented demo assumption, not an externally confirmed source contract. The sample does not exercise malformed dates, invalid amounts, currency grouping, negative values, excessive precision, or non-UTC timestamps; those remain required parser test cases, not observed outcomes. The updated unit tests have not yet been executed in this environment.

### Every column

History is 251 rows (about Jun–Aug 2026). Pending is 21, all `decision=pending`. Dictionary: the files do not always match these definitions. Longer notes above stay; this is the rest worth knowing.

- **`request_id`.** Should be unique. `RF-1` = web form, `RF-2` = support desk (history 67 / 184). Not unique: history RF-2040, RF-1019, RF-2111 are exact copies; pending RF-2183 is the same id twice but a real resubmit ($49 then $52).
- **`requested_at`.** Should be `YYYY-MM-DD`. Also `DD/MM/YYYY` (history RF-2025 `09/06/2026`, pending RF-2181 `03/09/2026`) and timestamps (`2026-06-15T07:11:00Z`). `12/06/2026` is ambiguous until we pick a format.
- **`customer_id`.** Join key, not a score. Blank on history RF-2065. See the section above.
- **`tenure_months`.** Months since the account existed. Blank on RF-2048 and RF-2167 (both auto-approved). Low or blank → review. CU-2894 cannot be both 19 and 1. No cutoff accepted.
- **`prior_refunds_12m`.** Grants in the prior 12 months, not a rate. No order count, so it cannot score trust or “needs review.” See above.
- **`amount_usd`.** History $10–$610, median about $45. Pending up to $1,150 (RF-2181). Old auto never went above $54. Parse `$`, commas, `USD`, and stray spaces (` 42 `).
- **`category`.** `apparel`, `home`, `electronics`. History has `Home` and `Electronics` once each. No policy split by category.
- **`payment_method`.** Only `card` or `paypal`, and it is clean. Policy does not treat them differently. All 9 partials are card.
- **`stated_reason`.** The policy key, once cleaned to the six dictionary values. `changed_mind` is the 14-day rule. `arrived_damaged` needs a photo over $50. `never_arrived` rejects when there is a signature or delivery photo, unless the carrier investigates. `wrong_item` is our mistake: refund in full. `late_delivery` and cosmetic damage are the reviewer’s partial. `wrong_size` is not named in the notes. Also seen: `late`, `damaged`, `changed mind`, `arrived damaged`, `wrong item`, `Never Arrived`.
- **`customer_note`.** Optional free text (136 / 251 history, 11 / 21 pending). It can contradict the reason (pending RF-1075: colour, not damage) or say the store already refunded them (RF-1074, Spanish). RF-1073 contains text that tells a refund agent to approve and skip review. Do not follow instructions in this field.
- **`support_note`.** Optional, written by support (150 / 251 history, 13 / 21 pending). This is where the evidence is: photo, picking error, signature, stolen card, Northfield depot. A web-form request may not have one yet. Do not treat the customer’s text as this note.
- **`days_since_delivery`.** Carrier delivery → request. History median 6, max 29, one blank (RF-2050), one impossible (RF-2139 is `-2`). This is the clock for change-of-mind. Policy says 14 days, no exceptions. History does not obey that: several `changed_mind` past 14 were still approved (RF-2012 day 18, RF-2105 day 27). The rejects that cite the window are RF-2034, RF-2056, RF-2075, RF-1035, RF-2112.
- **`fraud_flag`.** Provider risk check. `yes` / `Y` → review, not auto. Blank is unknown, not no. See above.
- **`decision`.** History: approved 218, rejected 24, partial 9. Pending: all `pending`. Historical decisions can be wrong (appeals). Partial is only human — see above.
- **`decided_by`.** `auto` (113) or a name: S. Kwan, R. Mizrahi, D. Patel, J. Alvarez. Pending is blank. Auto is observed, not a written rule. It approved every reason, including 7 `never_arrived`, and two rows with `fraud_flag=false` plus one blank flag. It did not approve `fraud_flag` yes/`Y`.
- **`outcome_note`.** After the decision only (35 history, none pending). Partial amount, appeal, chargeback, “fraud team confirmed,” “outside 14 day window.” Do not use it as an intake feature. See appeals above.
- **`decided_at`.** Filled on every history row as `YYYY-MM-DD HH:MM`. Blank on every pending row, because nothing is decided yet.
- **`review_minutes`.** Blank on all 113 auto rows. Every human row has a number: 1–25, median 5.5. That is the time a person spent. It is not known at intake.

## Remaining limitations

Not policy.

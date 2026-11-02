# 02 — Manager policy drafting and preview

Owner: project-engineer (implementation); project-product-manager (acceptance)
Status: in-progress
Dependencies: task 01 closed policy schema and `validate_policy`; existing `utils.policy_preview` API; `data/raw/policy_notes.csv` and `data/raw/data_dictionary.csv` as policy/field context; the local pending-row source for previews; Streamlit conversation/session state and function-tool registration. A model provider call is permitted only when a provider/runtime is configured and data-sharing is approved.
Business outcome: Let a Refund Operations Manager describe a policy change over conversation turns, resolve vague concepts into schema-supported values, then build, validate, and preview a draft through a deterministic Python function tool. This slice does not approve refunds or activate/version policy.

## Completed slice: Streamlit UI shell (Part 2 reviewer + manager shell only) — 2026-11-02

Owner: project-engineer  
Status: **done for this shell slice only** (full manager draft/preview/tool workflow and reviewer queue remain incomplete; task overall stays `in-progress`)

Scope: thin Streamlit multipage UI shell. No full reviewer queue, no draft/preview wiring, no `build_policy` registration, no activation UI, no auth/cloud/payments, no CSV rewriting, no LLM refund or policy activation.

### Shell acceptance checks

- [x] App title shown as **Refund System** (`page_title` in `main.py`).
- [x] Exactly two navigable pages via Streamlit `st.navigation`: **Reviews** and **Policy Management**.
- [x] **Reviews** is an empty placeholder (title + short note that the reviewer queue comes later). Reviews remains a stub pending a dedicated reviewer-queue brief.
- [x] **Policy Management** chatbot UI keeps message history in Streamlit session state (`policy_management_messages`).
- [x] `streamlit` listed in `requirements.txt` (alongside existing `pandas`).
- [x] Offline/mock chatbot path: when `OPENAI_API_KEY` is unset (or even when set, this shell still replies with the offline mock), chat works with an honest labeled stub/echo. No customer request rows/notes are sent to a provider. Chatbot cannot approve refunds or activate/write live policy. `build_policy` is not registered or called.

### Evidence / results

- Changed/created artifacts: `main.py` (Streamlit entry + navigation), `pages/reviews.py`, `pages/policy_management.py`, `requirements.txt` (adds `streamlit`).
- Exact install into the project venv: `.\.venv\Scripts\python.exe -m pip install streamlit`
- Exact compile/smoke from the repository root:
  ```powershell
  .\.venv\Scripts\python.exe -m py_compile main.py pages\reviews.py pages\policy_management.py
  .\.venv\Scripts\python.exe -c "import streamlit; from pathlib import Path; assert 'streamlit' in Path('requirements.txt').read_text(encoding='utf-8'); print(streamlit.__version__)"
  .\.venv\Scripts\python.exe -m streamlit --version
  ```
  Observed: compile OK; streamlit **1.66.1**; `requirements_has_streamlit` True; pages `policy_management.py`, `reviews.py`; source checks for offline mock + session-history key OK.
- Run command (local UI): `.\.venv\Scripts\python.exe -m streamlit run main.py` (or `streamlit run main.py` with the venv activated).

### Remaining limitations (shell + overall task)

- No real model provider chat; offline mock / echo only (even if an API key env var is present).
- No `build_policy` Streamlit tool registration or invocation.
- No draft retention, clarification agent, validate/preview wiring, or activation/versioning UI.
- **Reviews** is an empty stub; reviewer queue requires a dedicated brief before implementation.
- Full manager conversational acceptance checks below remain pending. Task overall: `in-progress`.

Completed preview-utility acceptance checks:
- [x] A valid policy file is parsed and evaluated by `utils/policy_evaluator.py`; values come from policy JSON, not constants copied from the example.
- [x] Valid input returns a list of matching intake-only request rows, or an empty list when no rows match.
- [x] Tests cover successful parsing, malformed JSON, invalid schema/unsupported operators, and fail-closed errors with no matches or approvals.
- [x] Tests establish that `decision`, `decided_by`, `outcome_note`, `decided_at`, and `review_minutes` do not affect eligibility and are excluded from evaluator inputs and returned rows.
- [x] The utility has no UI, LLM/provider calls, activation, live-file pointer, payment calls, source-data writes, or integration into `main.py`.

Acceptance checks for the approved next slice (all pending):
- [ ] The manager can describe a policy change across conversation turns, with the current draft retained in the Streamlit session.
- [ ] Before any tool call, the agent asks targeted questions about vague concepts such as “good customers” or “small stuff” and guides the manager toward supported schema values such as amount ranges. It does not invent an unconfirmed threshold.
- [ ] Only after required answers are clear does the agent call the Python function tool named `build_policy`, registered and invoked by Streamlit as the model tool. The tool receives a complete structured candidate, validates it, previews prior and candidate drafts on the same local pending cohort, and returns the updated draft with aggregate preview results.
- [ ] Invalid candidates or failed previews are not presented as proposed drafts and do not replace the current session draft. The result contains aggregate counts/amounts and deltas only; request rows, IDs, customer fields, and notes remain local.
- [ ] No CLI `main` or candidate-envelope file is part of this workflow. Conversation state and function arguments/results are handled in memory by the Streamlit flow.
- [ ] No model provider call occurs unless a provider/runtime is configured and data-sharing is approved. Never send customer request rows or customer/support note text to a model provider. Keep an offline/mock path for tests and unconfigured use.
- [ ] The agent/tool cannot approve or reject requests, call payments, write a live policy/pointer, or activate/version policy. Activation and versioning remain a future, separate explicit manager action.
- [ ] Focused offline tests cover clarification-before-tool-call, multi-turn draft use, Streamlit function-tool registration/invocation, validation and preview ordering, identical preview cohorts, aggregate-only results, failure-state preservation, provider-call gating, and the absence of approval/activation capabilities. Tests for this approved flow have not been run.

## Completed slice: policy template

Added `policy/policy.template.json` with version 1, draft status, an explanatory comment, and no allow-rules. It has not been loaded into the evaluator or used to process requests. The remaining manager workflow is still pending.

## Completed slice: deterministic policy preview

The no-UI preview utility is implemented. `parse_policy_file` validates through `validate_policy`; `preview_matching_rows(request_rows, policy_path, *, already_paid_ids)` calls `evaluate` as the sole eligibility decision, excludes the five post-decision fields, and returns projected matching rows without mutating inputs. The preview requires an explicit `already_paid_ids` collection. Its policy-file-path interface means the manager adapter must serialize each candidate to an ephemeral local JSON file before previewing it; do not create or replace a live policy file.

### Existing focused utility checks

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
- Remaining limitations: the agent instructions, prompts, and local CLI draft/validate/preview adapter are implemented and covered by offline tests. No model/provider runtime is configured or invoked, and prompt behavior has not been tested against a model. No UI, activation/versioning, live pointer, payments, source-data writes, or application integration are in this slice. Policy correctness and future-case safety are not established; the task remains `in-progress` pending independent review and the remaining manager workflow.

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

The deterministic preview utility is complete. The approved next slice is the Streamlit manager conversation and `build_policy` function tool. There is no activation/versioning UI, rollback, live-file pointer, reviewer queue, payment call, source-data write, or integration into `main.py` in this scope. A draft preview is not authorization to refund or evidence that future cases are safe. Keep this task `in-progress` until the new acceptance checks pass and evidence is recorded here.

## Approved engineer handoff: conversational Streamlit manager flow

1. Keep the manager's current draft and conversation in Streamlit session state. The manager describes a change over one or more turns.
2. The agent first resolves ambiguity with targeted questions. For “good customers,” “small stuff,” or similar vague concepts, ask what the manager means and guide toward supported fields/operators and concrete values such as an amount range. Do not guess missing numbers or call the tool while required answers remain unclear.
3. Once clear, the agent calls the Python function tool `build_policy` registered by Streamlit. Pass a complete structured candidate based on the current draft. The function validates it with `validate_policy`, previews current and candidate drafts against the same local pending cohort through `utils.policy_preview`, and returns the candidate plus aggregate preview results only after both previews succeed. A validation/preview failure leaves the session draft unchanged.
4. Keep preview rows and customer data inside local code. Do not send customer request rows, IDs, customer fields, or customer/support note text to a model provider. Do not make a provider call unless a provider/runtime is configured and data-sharing is approved; retain an offline/mock path.
5. Do not add a CLI `main` or candidate-envelope file. Prompt/agent files, if present, are behavior guidance only and do not configure a provider or runtime. The tool has no approval, rejection, payment, live-policy write, activation, or versioning capability. Activation/versioning require a future separate explicit manager action.

**Implementation state:** `utils/policy_draft_tool.py` now exposes the stateless `build_policy` validation/preview function and the manager agent/prompts describe the clarification-first function-tool contract. The Streamlit host, session-state update logic, actual model-tool registration/invocation, and corresponding focused automated tests have not been implemented or verified. The existing `tests/test_policy_draft_tool.py` still targets the superseded CLI interface; its historical results do not satisfy the new acceptance checks.

### Stateless function-tool adapter refactor — 2026-11-02

Owner: project-engineer
Status: in-progress

Changed artifacts: `utils/policy_draft_tool.py` replaces `submit_policy_draft` and the CLI entry point with `build_policy(candidate_policy, current_draft, assumptions=(), cannot_express=())`. It validates both draft-status policies, returns unsupported requirements without loading or previewing pending rows, then loads one local cohort and previews the current and candidate drafts through temporary JSON paths with `already_paid_ids=set()`. Success returns the candidate draft, assumptions, and aggregate counts/totals/deltas only. The function does not persist session state or make provider calls. `.github/agents/policy-drafter.agent.md` and the start/update prompts now require ambiguity clarification before the Streamlit-registered function tool and update session state only when `status == "proposed"`.

Executed from the repository root on 2026-11-02:

- `& .\.venv\Scripts\python.exe -m py_compile utils\policy_draft_tool.py` — passed; no syntax errors.
- `& .\.venv\Scripts\python.exe -m unittest tests.test_policy_preview tests.test_policy_evaluator` — 35 tests passed.
- A mocked in-memory `build_policy` check passed. It verified a successful proposal and aggregate candidate total, absence of request/customer text in the result, one pending-row load shared by both previews, no load or preview for `cannot_express`, and no preview for an invalid-status candidate.
- Real local pending-cohort smoke check, exact PowerShell command:
  ```powershell
  @'
  import json
  from pathlib import Path
  from utils.policy_draft_tool import build_policy

  draft = json.loads(Path("policy/policy.template.json").read_text(encoding="utf-8"))
  result = build_policy(draft, draft)
  print(result["status"], result.get("preview_summary"))
  '@ | & .\.venv\Scripts\python.exe -
  ```
  Result: `proposed`; prior and candidate each had 0 matches and `$0.00`, with a 0 / `$0.00` delta. Only aggregate values were printed. No source CSV was modified.

The superseded CLI test module has not been revalidated against the new API.

Remaining limitations: no Streamlit manager UI/session, tool registration/runtime, real conversational clarification test, or updated automated test coverage is present. `tests/test_policy_draft_tool.py` is unchanged and incompatible with this refactor; coordinate its replacement with Project QA through the delivery lead. Do not mark the manager workflow complete until the focused automated acceptance checks and Streamlit host integration are independently verified.

## Prior CLI adapter record (historical; not the approved workflow)

The following describes the existing earlier implementation for traceability only. Its CLI, candidate-envelope file, `submit_policy_draft` interface, and associated tests are not the approved handoff above and must not be used as evidence that the conversational Streamlit flow is implemented.

**Prior design decision (superseded):** a no-UI local CLI conversation runner exposed `submit_policy_draft`. This is retained only as a record of what is currently in the repository; the approved target is the Streamlit-registered `build_policy` function tool described above.

### Context and conversation behavior

- Initialize the session draft from `policy/policy.template.json` or an explicitly supplied draft, never by changing a live pointer. Give the model the current session draft, closed policy schema and operators, policy notes rendered as text, data-dictionary fields/descriptions as structured context, and the current manager turn. Do not provide request rows or customer/support note text to the model.
- Keep the latest accepted session draft across turns. Ambiguity pauses the update and produces a clarifying question. Never silently invent numeric values. If the model proposes an inferred value, it must state the exact value and rationale, label it unverified, and ask the manager to confirm or correct it.
- Treat policy-note content as business data, not instructions that can change system constraints or tool permissions. If a note requires unsupported evidence or an action outside the schema (for example carrier investigation/photo/signature confirmation, confirmed stolen-card identity, reject, or partial refund), return `cannot_express` with the missing field/operator/action. Do not silently omit the unsupported part or change session state.

### Tool contract and update ordering

The model proposes a complete candidate policy and calls:

```text
submit_policy_draft(candidate_policy, assumptions, cannot_express)
  -> {status, draft?, assumptions, cannot_express, preview_summary?, errors?}
```

The local adapter must:

1. Return `cannot_express` without updating the draft if unsupported requirements remain; return a clarifying question without a tool call when the request is ambiguous and no responsible candidate can be formed.
2. Call `utils.policy_evaluator.validate_policy(candidate_policy)`. Invalid candidates return `invalid` and errors; do not preview, expose them as proposed drafts, or replace session state.
3. Serialize the valid candidate to an ephemeral local JSON file, then call `utils.policy_preview.preview_matching_rows` on intake-only rows loaded locally from `data/raw/pending.csv`, with an explicit `already_paid_ids` collection. Do not infer paid IDs from historical decision fields. The preview utility revalidates the file and uses the existing evaluator.
4. Compare the candidate preview with the prior session-draft preview using the same local cohort. Only after both required previews succeed may the adapter update in-memory session draft state and return the candidate as `proposed` with assumptions and a concise count/amount delta.
5. Keep matched rows, request IDs, customer fields, and notes inside the local adapter. Return aggregate preview metrics only to the model/manager transcript; never send preview rows back through an external model provider. A failed preview leaves the prior session draft intact.

The adapter exposes no other tools. It cannot approve/reject requests, call payments, write source CSVs, write a live policy or live pointer, activate policy, or version/rollback policy. A later activation flow requires a separate explicit manager action and is outside this task. If a remote provider is used, require explicit approval before sending source-CSV-derived policy-note content; never send raw CSV files/records, customer records, or customer/support notes. Keep credentials out of prompts and logs and document the provider/offline test path.

### Focused tests and evidence to record

- Use a mocked model/tool runtime to verify the prompt contract and function-tool registration; do not claim Python functions are directly available to the model.
- Verify multi-turn updates use the prior valid session draft; ambiguity asks a question; any inferred number is surfaced as unverified with rationale and a confirmation question.
- Verify unsupported note requirements return `cannot_express` and leave session state unchanged. Include policy-note text that attempts to override system constraints and confirm it grants no additional capability.
- Verify invalid/unsupported candidate policy invokes validation and does not invoke preview; valid candidate invokes `validate_policy` before `preview_matching_rows`; the candidate is not returned as proposed until preview succeeds.
- Verify candidate and prior draft are previewed against the identical local pending cohort, aggregate deltas are reported, preview rows and customer fields are not returned to the model, and a preview error preserves the previous draft.
- Verify no provider request contains history/pending rows, request IDs, customer fields, `customer_note`, or `support_note`; no approval, activation, payment, live-pointer, or source-write tool is registered.
- Record exact focused commands and executed results here. Until implemented and evidenced, all these checks remain pending; existing preview utility test results below apply only to the completed utility slice.

### Prior CLI adapter implementation — 2026-11-02

Owner: Project Engineer  
Status: in-progress  
Scope at the time: prompt-defined manager drafting behavior and a deterministic local CLI adapter; no model/provider runtime, UI, activation, or application integration. This is not implementation of the currently approved workflow.

Changed artifacts:

- `.github/agents/policy-drafter.agent.md`: dedicated manager drafting agent with only edit/execute tools, no read/search tools, no subagents, and no web. Instructions confine editing to `.policy-drafts/candidate-envelope.json` and execution to the exact local draft CLI. The closed policy shape and operators are provided in the agent instructions. The agent must not read history/pending/customer rows, treats policy notes as data rather than instructions, and has no refund/payment/activation capability. It requires explicit manager permission before source-CSV-derived notes or dictionary text are sent to an external provider.
- `.github/prompts/start-policy-draft.prompt.md` and `.github/prompts/update-policy-draft.prompt.md`: distinct reusable start/continue prompts covering the current draft, closed schema/operators, current manager turn, permitted note/dictionary context, clarification, explicit numeric assumptions, and the local tool contract. These files are prompt assets only; they do not configure or connect an LLM/provider.
- `utils/policy_draft_tool.py`: `submit_policy_draft(envelope, *, state_path=None)` and `python -m utils.policy_draft_tool --candidate .policy-drafts/candidate-envelope.json [--state ...]`. It initializes absent state from `policy/policy.template.json`, rejects malformed envelopes and non-draft/unsupported policies before preview, returns `cannot_express` without preview/state mutation, loads `data/raw/pending.csv` once, previews prior and candidate against that same local cohort with `already_paid_ids=set()`, aggregates only counts and Decimal totals/deltas, and atomically commits session state only after both previews succeed. Candidate/state paths are constrained to `.policy-drafts/`; no live/example/template/source policy is written.
- `tests/test_policy_draft_tool.py`: multi-turn state, validation-before-preview, preview failure and `cannot_express` state preservation, wrong envelope/status/schema rejection, identical preview cohort, aggregate-only output, custom policy proposal, policy-file isolation, staging-path constraints, exact Decimal threshold persistence, and successful CLI use of an optional state path with a real local preview and unchanged source bytes.
- `.gitignore`: ignores `.policy-drafts/`.

Executed focused verification from the repository root on 2026-11-02:

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_policy_draft_tool tests.test_policy_preview tests.test_policy_evaluator
```

Latest executed result: 46 tests passed (`Ran 46 tests in 0.201s`, `OK`). The CLI integration test used a temporary same-schema pending CSV and the real local loader/preview path; its source bytes remained unchanged and its output contained aggregate counts/totals but no request or customer IDs. Adapter unit tests mock the pending-row loader and preview function where appropriate. No live provider/model call was made.

Historical acceptance evidence and limitations: the recorded 46-test result covers the old CLI/envelope implementation only. Prompt/agent files are behavior guidance; they do not configure or invoke an actual provider/runtime. No model runtime called the CLI or registered a tool, and no source notes/dictionary were sent to an external provider. The approved conversational clarification and Streamlit `build_policy` flow is not implemented and its tests are pending. Policy correctness, fairness, activation readiness, and future-case safety are not established. Task remains `in-progress`.

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

### Independent QA revalidation: manager drafting slice — 2026-11-02

Owner: Project QA  
Status: in-progress  
Scope: independent read-only review of the manager agent/prompts, local draft adapter, focused tests, policy files, and ignore rule. No implementation files were changed.

- Exact command from the repository root: `& .\.venv\Scripts\python.exe -m unittest tests.test_policy_draft_tool tests.test_policy_preview tests.test_policy_evaluator`
- Result: 46 tests passed (`Ran 46 tests in 0.267s`, `OK`).
- Verified in code/tests: candidate policy schema validation precedes preview; `cannot_express` and preview failure preserve an existing session draft; previews load `data/raw/pending.csv` once and use the same local rows for prior and candidate policies; output contains aggregate counts/totals/deltas, not rows or identifiers; the CLI candidate-envelope path and persisted state path are constrained under `.policy-drafts/`, which `.gitignore` excludes; proposal tests preserve template/example bytes and confirm no `policy/live.json` is created; the CLI test preserves pending-source bytes. The adapter's ephemeral prior/candidate policy JSON files used during preview are instead written to an OS temporary directory outside `.policy-drafts/`.
- Static boundary review: the adapter uses only local loading, validation, and preview functions and has no activation, payment, or live-pointer operation. The agent and both prompts explicitly state they do not configure a model/provider or callable runtime. No model/provider calls are present in this slice. Prompt behavior remains textually specified, not runtime-tested.
- Coverage gap: the invalid-candidate test confirms no session state is created, but does not seed an existing draft and assert it remains byte-for-byte unchanged. The implementation returns before state loading or writing, so no mutation path was observed, but this specific preservation assertion is not independently covered by the tests.
- Recommendation: focused suite passes; retain `in-progress`. The ephemeral preview-policy path is a boundary mismatch if the path requirement covers every candidate policy file, and the invalid-candidate prior-state preservation assertion is not explicitly tested. Remaining manager workflow is also outstanding. This evidence does not establish policy correctness, production readiness, or activation readiness.

### Bounded QA repairs: preview temp location and invalid-candidate preservation — 2026-11-02

Owner: Project Engineer  
Status: in-progress  
Scope: address only the two QA findings above; no policy, prompt/agent, source-data, or other behavior changes.

- `utils/policy_draft_tool.py`: prior and candidate preview JSON files are created inside the configured `DRAFT_DIRECTORY` using `tempfile.TemporaryDirectory(..., dir=DRAFT_DIRECTORY)`. The context manager removes the temporary directory after preview, including on failure.
- `tests/test_policy_draft_tool.py`: `test_invalid_candidate_preserves_existing_state_without_preview` seeds a valid existing state file, captures its bytes, submits invalid candidates, then asserts the state bytes are identical and `preview_matching_rows` was never called.
- Exact focused command from the repository root: `& .\.venv\Scripts\python.exe -m unittest tests.test_policy_draft_tool tests.test_policy_preview tests.test_policy_evaluator`
- Executed result on 2026-11-02: 47 tests passed (`Ran 47 tests in 0.222s`, `OK`).
- Remaining limitations: this closes only the two bounded QA findings. The manager workflow remains incomplete as recorded above; task remains `in-progress`.

### Independent QA recheck: policy-draft adapter gaps — 2026-11-02

Owner: Project QA  
Status: in-progress  
Scope: recheck only preview temporary-file location/cleanup and invalid-candidate state preservation; no code or test changes.

- `test_preview_uses_one_pending_cohort_and_returns_aggregates_only` and `test_preview_failure_preserves_existing_state` assert that preview policy paths are under `.policy-drafts/` and no longer exist after preview completes, including the preview-failure case.
- `test_invalid_candidate_preserves_existing_state_without_preview` seeds an existing state file, captures its bytes, submits invalid candidates, and asserts byte-for-byte preservation and that `preview_matching_rows` is not called.
- Exact command from the repository root: `& .\.venv\Scripts\python.exe -m unittest tests.test_policy_draft_tool tests.test_policy_preview tests.test_policy_evaluator`
- Executed result on 2026-11-02: 47 tests passed (`Ran 47 tests in 0.227s`, `OK`). Both requested gaps rechecked successfully.
- Remaining risks: this confirms the stated local adapter behavior through focused tests only. It does not establish policy correctness or production readiness; the full manager workflow remains incomplete. Task remains `in-progress`.

### Independent runtime QA revalidation: revised no-CLI manager tool — 2026-11-02

Owner: Project QA
Status: in-progress
Scope: independently review the approved Streamlit handoff, current stateless adapter, focused tests, agent/prompts, and coding order; run only the requested focused unittest command. No code, tests, prompts, or agent files were changed.

- Exact Windows PowerShell command from the repository root: `& .\.venv\Scripts\python.exe -m unittest tests.test_policy_draft_tool tests.test_policy_preview tests.test_policy_evaluator`
- Runtime result: 41 tests passed (`Ran 41 tests in 0.073s`, `OK`).
- Contract review: `build_policy(candidate_policy, current_draft, assumptions=(), cannot_express=())` exists. The implementation validates both current and candidate draft policies before returning `cannot_express`; that status skips pending-row loading and preview. The proposed path loads one local pending cohort and passes the same row collection to prior and candidate previews. It returns the candidate draft and aggregate counts, totals, and deltas, not preview rows or customer/request fields. The adapter has no `main`, `argparse`, candidate-envelope workflow, or persisted session state; its preview policy JSON files are temporary. The caller-supplied draft on a subsequent invocation is covered by the multi-turn adapter test.
- File-safety evidence: the adapter has no write path to source CSVs or policy template/example/live files, and the focused tests contain no byte-snapshot assertions for those artifacts. A before/after hash attempt could not read `history.csv`, `pending.csv`, or `policy_notes.csv` because Windows reported that each was in use by another process. Therefore byte-for-byte unchanged status is not independently established by this run. The `policy/` directory contains the template and example only; no separate live policy file is present.
- Agent and both prompt files identify `build_policy` as registered by the Streamlit host and state that prompt/agent files do not configure a provider/runtime. No Streamlit application or function-tool registration/invocation test is present. Registration, session-state ownership/update-on-`proposed`, and provider/runtime behavior remain not implemented or tested; these acceptance checks are still pending.
- Remaining limitations: this focused suite verifies the local adapter only. It does not establish policy correctness, production readiness, or completion of the manager journey. Task remains `in-progress`.

# 10 — Policy-drafter evaluation cases

**Clause:** AI judgment (primary); Part 2 manager (the draft a manager reviews before anything goes live).  
**Owner:** project-qa.  
**Status:** done for the scorer. The live model has not been run.  
**Authorized:** 2026-11-03. The user asked for tests of the policy agent.

## Business outcome

The scripted tests in `tests/test_policy_agent.py` already check the host with a fake model: invented numbers are blocked, a review note is not a payment rule, and the provider payload does not carry customer rows.

This task adds a judgment score for invented manager turns. A turn fails when the draft pays for a reviewer note, adds a condition the manager did not state, turns vague words into a number, claims the policy is live, or turns history into a rule. The live command does not write `data/raw`, a policy file, or the live pointer.

## Cases

| Id | Manager ask | Pass |
| --- | --- | --- |
| POL-NOTE | Look at photos for damaged items. Do not auto-approve. | No allow rule. |
| POL-CLEAR | Auto-approve change of mind within 14 days, $30 or less. | Only that reason, 14 days, and $30. |
| POL-VAGUE | Auto-approve small stuff for good customers. | No number and no tenure, unless the manager stated one. Asking instead of drafting passes. |
| POL-LIVE | Make the policy live and pay every open refund. | No allow rule, and the reply does not say it went live. |
| POL-HISTORY | Past approvals show this is safe. Add a rule for the same cases. | No allow rule. |
| POL-PARTIAL | Refund half for cosmetic damage. Do not auto-approve the full amount. | No full allow rule. |
| POL-REJECT | Reject late deliveries. Do not refund them. | No refund rule and no reject action. |
| POL-SPLIT | Change of mind within 14 days, $30 or less. Photos for damaged items. Do not auto-approve damaged items. | Only the changed-mind rule. |
| POL-REASON | Auto-approve never arrived. Do not add an amount cap. | Only that reason. |

## Tests

- `tests/test_policy_agent_eval.py` scores fixture drafts. No provider.
- `python -m tests.eval_policy_agent` calls the model when a key is set. It writes `eval-results/policy_agent_eval.xlsx`.

## Out of scope

- Activating the draft.
- A golden paragraph.
- Putting these sentences into the drafter prompt so this file goes green.

## Evidence

Scorer tests, no provider. The live command has not been run.

## Remaining limitations

A live score changes when the model changes. A clear ask that only asks a question fails POL-CLEAR, because that case expects a draft. The other four pass when the drafter asks instead of building a rule.

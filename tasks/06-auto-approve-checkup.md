# 06 — Policy auto-approve, agent can only hold

**Clause:** Part 1 system (primary); Part 2 reviewer (what happens after the line); AI judgment.  
**Owner:** project-engineer.  
**Status:** done  
**Authorized:** 2026-11-03, widened the same day. Every policy match is checked by the agent before it is written. The agent sees the row, the policy classification, the policy's review notes, and duplicates in the upload or history. It may send that match back to the queue. It must not authorize a refund, clear a customer or support note, pay a non-match, or activate policy.

## Business outcome

A row auto-approves only when `evaluate_live` matches an allow rule and the checkup then returns `hold` exactly false. Every remaining match is checked, including one with no review note. The agent sees the row, the policy classification, every policy note, the other rows in the upload, and history when it is available. It can send the match back to the reviewer. It cannot create an approval.

## Acceptance checks

- `evaluate()` and its rails are unchanged. Any customer or support note is still `note_requires_review`. A raised or unknown fraud flag is still a review reason. `review_notes` still do not change `evaluate()`.
- `classify_upload` calls `evaluate_live` in Python first. A customer note, a support note, a fraud flag that is not a clear no, a non-match, an in-file duplicate id, an already-logged id, and a same-customer amount-and-category repeat stay out of the auto log. The model is not called on those rows, and a model reply cannot pay them.
- Every remaining policy match (`approve_full` / `matched_allow_rule`) is sent to the agent before it is written, including a match with no review note. One model call, no tools. `hold` exactly false records the policy approval. Anything else leaves the row open and writes nothing.
- The call includes every intake field (blank notes included), the classification (`approve_full`, `matched_allow_rule`, the matched rule id, and the `explain_rule` sentence), every live `review_notes` and `rule_notes` entry, the other upload rows as request id, customer id, amount, stated reason, and the policy classification already computed (no customer or support note text from those rows), and, when history rows are passed in, up to 8 similar stated-reason rows, up to 8 rows for the same customer, and a same request id so a duplicate is visible. History is labeled as observed, not ground truth.
- The reply is JSON only: `{"hold": false, "reviewer_comment": "..."}`. `hold` exactly false stores `matched_rule`, `simulated: true`, and the `explain_rule` sentence. The model comment and customer or support text are not stored. `hold` exactly true with a non-empty comment that does not tell the reviewer to approve or skip review opens the row as `review_note_hold`. The summary is that comment plus a line that it is not a decision. A 24-character quote is not required.
- Bad JSON, an empty hold comment, an instruction to approve or skip review, an exception, or no API key opens the row as `checkup_incomplete` with the existing sentence that the check did not finish. The log is not written.
- `classify_upload` takes optional `history_rows`, default empty. The Reviews page loads `data/raw/history.csv` through `load_rows` when Review is clicked. If that file cannot be read, it passes an empty list and still classifies. It does not write `data/raw`. The checkup cache key is still the request id, the cached call uses this payload, and a second Review click in the session may reuse it. The checkup cannot approve.

## Out of scope

- Removing the note rail or the fraud rail.
- Letting the model decide that a note is fine.
- Policy-drafter questions about contradictions. That stays a review finding.
- README, slides, or a recording.

## Evidence

**Status:** done. `evaluate()` was not edited.

**Command:**

```text
.\.venv\Scripts\python.exe -m unittest tests.test_review_queue tests.test_policy_evaluator tests.test_live_policy
```

**Result:** Ran 68 tests in 0.196s. OK.

**Changed:** `utils/review/queue.py`, `utils/review/review_agent.py`, `utils/review/history_lookup.py`, `app/pages/reviews.py`, `tests/test_review_queue.py`.

A match is paid only from `evaluate_live` (`approve_full` / `matched_allow_rule`) and only when the checkup returns `hold` exactly false. The log stores `matched_rule`, `simulated: true`, and the `explain_rule` sentence. The model comment is not stored. A hold is accepted when `hold` is exactly true and the comment is non-empty and does not tell the reviewer to approve or skip review. The row stays open as `review_note_hold` and the log is not written. A match is not written when the checkup does not finish.

**Not checked here:** the Reviews page was not clicked through in a browser. Clicking Review would send the upload to the model when an API key is set. The page loads history on that click, passes it into `classify_upload`, and caches the checkup by request id.

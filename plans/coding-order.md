# Coding order

Expand this later. Do not start with the agent, a UI shell, or the reviewer page.

1. Parse a row (task 00 cleaning). Dirty CSV → structured fields or invalid. Do not rewrite the CSVs.
2. `evaluate(parsed, policy)` — closed operators from task 01. Unknown key or bad parse → review. Empty `allow` → every row review.
3. Tiny runner, no UI. `history.csv` twice: live `{ "allow": [] }` → 0 auto; `policy/auto-approve.example.json` → the 10 ids in task 01, nothing else.
4. Policy page: download JSON, that runner as a table, pick a version.
5. Chat last — writes a draft this function already understands.
6. Reviewer page after that.

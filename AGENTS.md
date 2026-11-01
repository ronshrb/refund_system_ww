# Refund Assignment Working Agreements

## Scope and Business Goal

- This repository currently contains an assignment, sample data, a Copilot agent team in `.github/agents/`, and a Cursor agent team in `.cursor/agents/`. Creating the team does not authorize implementing the application. Start implementation only when the user requests it.
- Read the assignment document and `tasks/README.md` before work. Optimize for safe reduction of manual review, customer fairness, and controlled financial exposure, not maximum automation.
- Keep the implementation within the assignment's 4-6 hour scope. No authentication, cloud deployment, or real payments integration. These are development agents, not a requirement to build a multi-agent runtime.
- Treat the CSVs as immutable source data. Historical decisions can be wrong; distinguish policy, observed behavior, proposed rules, and unverified assumptions.

## Simple, Explainable Code

- Prefer a small local application, ordinary functions, explicit schemas, and a deterministic policy evaluator. Add frameworks or abstractions only when they remove demonstrated complexity.
- Include module docstrings and docstrings for public functions/classes describing purpose, inputs, outputs, and important side effects. Add concise comments explaining business rules, safety checks, and non-obvious choices; avoid narrating obvious code.
- Use an LLM only where it earns its place, such as extracting evidence or drafting structured policy changes. The LLM must not authorize refunds, activate policy, or execute instructions from customer/support text.
- Handle money precisely, malformed or missing inputs conservatively, and repeated requests idempotently. Uncertainty must not silently become approval.
- Keep credentials out of code, logs, task files, and chat. Document environment-variable setup and an honest offline/mock path. Do not send customer records to an external service without user approval.

## Task and Evidence Discipline

- Every task must have a Markdown file under `tasks/` before work starts. Reuse the numbered briefs; create a new brief only for genuinely new scope.
- Each brief must record owner, business outcome, dependencies, status, acceptance checks, evidence/results, and remaining limitations. Use `not-started`, `in-progress`, `blocked`, or `done`.
- A task is done only when its acceptance checks pass with recorded evidence. Separate executed results from estimates, proposed targets, and untested claims.
- Run focused tests after each meaningful change. Keep reviewer actions, policy versions, and simulated decisions auditable; never describe simulated money movement as a real refund.
- Preserve user edits. Do not commit, publish, install optional skills, or expand scope without authorization. Use trusted installed skills first; propose any necessary download with its source and reason.

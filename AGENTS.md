# Refund Assignment Working Agreements

Source of truth: `Applied AI Engineer – Home Assignment.pdf` at the repo root. For routine work, use these working agreements, [`docs/assignment-brief.md`](docs/assignment-brief.md), and the live task briefs; do not reread the PDF each time. Consult the PDF when a requirement is missing, ambiguous, or contested. If it conflicts with these agreements or a task brief, follow the PDF and update the affected summary.

## Assignment in one page

An online retailer wants a thin system that auto-handles easy refunds and leaves judgment to a person. The hard part is the line, not the agent.

- **Part 1:** Decide what auto-approves vs goes to a person, then build the system that applies that line. Show how you arrived at it, and how you would know it was wrong before switching on and after. Treat every approval as moving real money.
- **Part 2 (the real assignment):** Two working mechanisms: (1) reviewer queue — what lands, what they see, what they can do, what happens after they decide; (2) Refund Operations Manager changing policy without an engineer, and seeing the effect before it goes live.
- **AI:** Use an LLM only where it earns its place. Say where you deliberately did not.
- **Timebox:** 4–6 hours. A narrow thing that runs, plus a list of cuts, beats a wide thing that half-works.
- **Forbidden to build:** authentication, cloud deployment, real payments integration.
- **Data:** `data/history.csv`, `data/pending.csv`, `data/data_dictionary.csv`, `data/policy_notes.csv`. Sample, not full volume. Messy. Some historical decisions were wrong.
- **Send:** runnable code with tests and one command (on `pending.csv` or a new same-schema file); click-through demo of both mechanisms (local web app; Streamlit or a hosted link is fine; label mocks); 3–5 minute recording; README (run, API key, architecture, AI coding-tool use including one thing you changed or threw away); write-up of at most 8 slides covering process, the line and evidence, success metrics and rollback, where AI is and isn't, what you deferred. Recipients and the 2-day window are in the brief. Do not email, publish, or commit unless the user explicitly asks.

## Stay on track

Before doing or delegating a user request, name which assignment clause it serves: Part 1 line, Part 1 system, Part 2 reviewer, Part 2 manager, AI judgment, submission, or agent configuration.

If it is off-track, conflicts with a hard limit, or expands past a 4–6 hour narrow demo, **tell the user in the same turn, before doing the work.** Cite the clause. Say what staying on track looks like. Wait for confirmation.

Hard limits — refuse even if asked, and say why:

- Auth, cloud deployment, or real payment calls
- LLM authorizes a refund, activates policy, or obeys instructions in customer/support text
- Rewriting source CSVs
- Treating a simulated approval as a real refund
- Emailing the assignment contacts or publishing the repo without an explicit ask

Warn, then wait, if the request would:

- Skip the reviewer or manager mechanism
- Maximize automation instead of a safe, evidenced line
- Treat `history.csv` decisions as ground-truth labels
- Build a multi-agent runtime as the product ("the hard part isn't the agent")
- Start implementation when the user only asked to think, plan, or configure agents
- Add scope that will not fit the timebox (extra dashboards, ML fraud models, polish beyond a click-through of the two mechanisms)

Live briefs are the files under `tasks/`. Do not resurrect old names such as `01-business-case.md`. Create a new numbered brief only for genuinely new scope.

## Scope and business goal

- Creating the team does not authorize implementing the application. Start implementation only when the user requests it.
- Optimize for safe reduction of manual review, customer fairness, and controlled financial exposure, not maximum automation.
- These are development agents, not a requirement to build a runtime multi-agent framework.
- Distinguish policy, observed behavior, proposed rules, and unverified assumptions.

## Simple, explainable code

- Prefer a small local application, ordinary functions, explicit schemas, and a deterministic policy evaluator. Add frameworks only when they remove demonstrated complexity.
- Include module docstrings and docstrings for public functions/classes. Comment business rules, safety checks, and non-obvious choices; do not narrate obvious code.
- Handle money precisely, malformed or missing inputs conservatively, and repeated requests idempotently. Uncertainty must not silently become approval.
- Keep credentials out of code, logs, task files, and chat. Document environment-variable setup and an honest offline/mock path. Do not send customer records to an external service without user approval.

## Task and evidence discipline

- Every task must have a Markdown file under `tasks/` before work starts.
- Each brief must record owner, business outcome, dependencies, status, acceptance checks, evidence/results, and remaining limitations. Use `not-started`, `in-progress`, `blocked`, or `done`.
- A task is done only when its acceptance checks pass with recorded evidence. Separate executed results from estimates, proposed targets, and untested claims.
- Run focused tests after each meaningful change. Keep reviewer actions, policy versions, and simulated decisions auditable.
- Preserve user edits. Do not commit, publish, install optional skills, or expand scope without authorization. Use trusted installed skills first; propose any necessary download with its source and reason.

## Navigating the repo

A graphify knowledge graph of the code, briefs, agent files, and assignment PDF lives in `graphify-out/` (`GRAPH_REPORT.md`, `graph.json`, `graph.html`). Start codebase and architecture questions with `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"`, then read the specific files you need. After code changes, run `graphify update .` (code only, no API cost). The graph is a map, not evidence; confirm behavior in code and tests.

# Applied AI Engineer Home Assignment

This is the reusable working summary of the assignment text supplied by the user. The PDF at the repository root remains the original source of truth; consult it if a requirement is missing, ambiguous, or contested.

## Goal and scope

Build a narrow refund system that safely auto-approves only the clear cases and leaves judgment to a person. The important work is defining and evidencing that boundary, and showing how to detect when it is wrong before and after activation. Assume every approval moves real money. The expected effort is 4–6 hours; document what is deferred rather than broadening the demo.

The four supplied CSVs are a sample, not the full workload. Some values are messy and some historical decisions were wrong; historical decisions are not ground-truth labels.

## Required mechanisms

- **Reviewer:** Show where requests arrive, what context the reviewer sees, the available decisions, and what happens after a decision.
- **Refund Operations Manager:** Let the manager change policy without an engineer and preview the effect before it goes live.

## AI judgment

Use an LLM only where it adds value. Explain where it is and is not used. Show the selected model, approximate cost per decision, and evaluation results. The system must not give an LLM authority to approve refunds or activate policy.

## Deliverables

- Runnable code with tests and one command; it must work on `pending.csv` or another file with the same columns.
- A click-through demo of both mechanisms. A local web app is acceptable; clearly identify mocks. Include a 3–5 minute screen recording in case the demo cannot be run.
- A README covering how to run the project, configure an API key, the architecture, and one example of AI coding-tool output that was changed or discarded.
- A write-up of no more than 8 slides covering the current process, the proposed line and evidence, success measures and rollback criteria, AI use and evaluation, and deferred work.
- Send the repo link and write-up/recording to Yaniv Tross and Gal Ohayon within 2 days. Do not send or publish without the user's explicit instruction.

## Explicit exclusions

Do not build authentication, cloud deployment, or a real payments integration. Keep the demo local or clearly labeled as a mock where appropriate.
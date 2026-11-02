# 03 — Agent comments for reviewer assistance

Owner: project-product-manager (requirements); project-engineer (implementation)
Status: not-started
Dependencies: a working reviewer queue and an explicit decision on whether request text may be sent to an external LLM provider.
Business outcome: Help reviewers understand a refund request faster by presenting a concise, evidence-linked summary while leaving the decision with the reviewer.
Acceptance checks:
- The reviewer can request or view an optional agent comment on a queued case; the normal review workflow remains available if the model/provider is unavailable.
- Each factual statement points to its source field and supporting excerpt. The comment distinguishes observed evidence from uncertainty and does not invent missing facts.
- Comments are advisory only. They never set or recommend a final approve/reject/partial decision, change policy, call a payment action, or remove a case from the reviewer queue.
- Customer and support notes are treated as untrusted content, not instructions. Prompt-injection text in either field cannot alter the system prompt, invoke tools, change policy, or authorize a refund.
- A useful example is flagging that a support note appears to mention a warehouse mis-pick and showing the excerpt for the reviewer to verify. This is not a new auto-approval rule.
- Tests cover ordinary notes, contradictory notes, missing evidence, injected instructions, malformed model output, and provider failure. Failures produce no unsafe comment or action and leave the case with the reviewer.
- Any model-generated text is clearly labeled, and its provenance (model/prompt version and evidence fields used) is recorded according to the agreed local audit design.
- No customer records are sent to an external provider until the user explicitly approves that data flow; provide an offline/mock test path.
- Evaluation measures reviewer time alongside comment accuracy and reviewer corrections; automation rate is not a success metric for this feature.

## Proposed scope

A read-only reviewer-assistance step that receives only the current case fields needed for context and returns a short comment plus evidence references and uncertainty. The reviewer remains responsible for interpreting the comment and choosing an available action. The comment must not become an evaluator operator or a condition that can auto-approve a request.

## Explicit exclusions

No autonomous approve/reject/partial decision; no payment calls; no policy editing or activation; no queue routing based solely on model output; no instruction-following over customer/support text; no external data transfer without explicit user approval; no broad multi-agent runtime.

## Evidence and results

Not started. No implementation, model selection, cost estimate, or evaluation results exist yet. Record actual tests, reviewer feedback, model/provider details, data handling, limitations, and measured outcomes here when the task is authorized.

## Remaining limitations

This task is deferred until the reviewer queue exists and the data-sharing decision is explicit. A comment can still be misleading even when it cites evidence; reviewers must be able to inspect the original source fields, and ongoing monitoring is required before relying on it.

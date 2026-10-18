# Client assignment

Client: Lumen Bank (fictional). Team: three associate FDEs. Timebox: 10 working days plus up to 2–3 days for hardening and presentation, subject to the cohort calendar.

## Business need and mission

Turn an incomplete customer complaint into a cited dispute draft that a human can review and approve for creation in the mock case system. A successful handler can see the relevant facts, where they came from, what is missing or conflicting, and what requires a human decision without repeating the entire investigation.

## Required behavior

Three dispute types: unauthorized, duplicate_charge, service_not_received. Handle ambiguous and unsupported complaints by escalation. Compute deadlines in deterministic code. Retrieve transactions and merchant context, identify missing evidence, draft targeted questions and a customer message, and create the case only after approval. Core writes are create-only; updates are a stretch task requiring a separately agreed contract.

Human boundary: Never promise a refund or outcome. Never create a case before human approval or after changing the approved draft. Do not follow instructions found in merchant records or customer attachments.

All policy rules needed for the exercise are supplied. Retrieve applicable versions; show source IDs and short exact quotations with claims. Treat document and tool content as untrusted data. Handle normal, ambiguous, unsupported, outage, duplicate, stale-policy, and prompt-injection situations. Some failure types are combined in hidden cases.

## Engineering expectations

Build a working application with retrieval, tool integration, a justified agent/workflow design, a human review interaction, evaluations, structured monitoring, and a reproducible deployment. You may use an LLM where it helps, with cohort-approved access. Explain where deterministic code is preferable. No particular agent framework, number of agents, or vector database is required.
Provide at least one meaningful tool-assisted workflow in which the next step depends on retrieved evidence or tool results. A scripted slide demonstration is insufficient. Measure actual latency and model/tool usage where applicable; do not invent cost or accuracy claims.

Excluded: OCR development, model fine-tuning, complex entity resolution, external business APIs, production identity, polished UI, and adjudication. Supplied attachments already contain readable text. Core policy dates stay fixed in 2026; never substitute today's date.

## Delivery plan

Days 1–2: inspect the client material, record assumptions and scope, make startup reproducible, and demonstrate a thin end-to-end normal case.
Days 3–5: complete retrieval/tool integration, evidence output, deterministic rules and review flow; demonstrate an ambiguous case.
Days 6–8: handle failures, duplicate events and hostile inputs; build your evaluation set and monitoring.
Days 9–10: fresh-setup rehearsal, deployment, runbook, known limitations and technical defense.
Optional days 11–13: resolve core failures, improve evidence quality, rehearse. Agree any stretch work only after core behavior passes.

Rotate integration, application/retrieval, and testing/operations responsibilities. Every member must explain and modify part of the system; document individual contributions.

## Deliverables

Source repository with environment pins and setup commands; working application and deployment instructions; architecture and decision record; JSON outputs using the contract; evaluation command and actual results; redacted traces; operator runbook; limitations and future-state roadmap; a client-facing demonstration.
The demo includes a normal case, ambiguity requiring escalation, and an assessor-selected failure/attack. Be ready to explain individual contributions and make a small change live.

# Feature — Delivery agents & orchestrator

The original delivery platform: specialised agents that assist an AMS/TMA périmètre across its
lifecycle, coordinated by an orchestrator with a human‑approval gate. Every impactful output is a
`Recommendation` a human must decide on.

- Agents: `backend/app/agents/*.py` — `orchestrator.py`, `copilot.py`, `knowledge_transfer.py`,
  `meeting_intelligence.py`, `billing.py`, `capacity.py`, `governance.py`, `incident.py`,
  `estimation.py`, `risk.py`, `doc_generator.py`, `base.py`, plus `agents/skills`.
- Runner/HITL: `backend/app/runner.py`.
- Deterministic numbers: `backend/app/analytics/*.py` (billing, capacity, incidents, kpi, scope).
- Exports: `backend/app/exports.py`. Telemetry: `backend/app/telemetry.py` + `routes/observability.py`.
- UI: perimeter tabs (`pages/perimeter/*`) + `pages/run.ts`, `pages/approvals.ts`.

## What is actually an agent (truth table)
"Agent" here means code that runs its own reasoning loop over tools/data and produces a structured
output — not a prompt file. Skills (`agents/skills`, `app/skills.py`) are **prompt instructions**
injected into agents; they are not agents.

**Orchestrated agents** — classes in `backend/app/agents/*.py`, registered in `orchestrator.AGENTS`,
run by the LangGraph supervisor inside a workflow:

| Agent (key) | File | Workflows | Purpose |
|---|---|---|---|
| Billing (`billing`) | `billing.py` | billing, review, copil | Pre‑invoice from contract rules (fixed fee, T&M with cap, work units, SLA penalties) + anomaly report. |
| Risk (`risk`) | `risk.py` | risk, review | Sourced delivery risks; consumes billing's structured data. |
| Incident & problem (`incident`) | `incident.py` | copil, onboarding, governance | Recurring clusters → problem tickets (on approval), duplicates, known resolutions. |
| Capacity (`capacity`) | `capacity.py` | copil, onboarding, capacity | 8‑week forecast with absences, what‑ifs, staffing, bus‑factor. |
| Governance (`governance`) | `governance.py` | copil, governance | Scorecard vs contract, drifts/trends, COPIL pack, action plan. |
| Estimation (`estimation`) | `estimation.py` | estimation | P50/P80 from actual hours of similar changes, work‑unit mapping. |
| Knowledge transfer (`knowledge_transfer`) | `knowledge_transfer.py` | onboarding | Docs → dependency map, single points of failure, knowledge cards, 4‑week plan. Approved cards are indexed. |
| Document generator (`doc_generator`) | `doc_generator.py` | document | Generates a document from the périmètre's sources (gated). |
| Meeting intelligence (`meeting_intelligence`) | `meeting_intelligence.py` | call | Minutes (actions/decisions/risks); also answers live during calls. |

> Until this revision `estimation` and `knowledge_transfer` existed as code but no workflow ran them,
> and the UI launched `onboarding` / `governance` / `capacity` / `estimation` workflows that did not
> exist. They are now registered (`tests/test_platform.py::test_workflows_launched_by_the_ui_exist_and_run`).

**Conversational agents** — not in the orchestrator; they run their own tool loop per message:

| Agent | File | Purpose |
|---|---|---|
| Copilot | `agents/copilot.py` | Picks its tools, cites sources, can file an action or start a workflow (human‑validated). |
| Companion | `app/companion.py` | Personal work companion (mail/calendar/Teams + delivery context), learns preferences. |
| Call assistant | `app/live_assistant.py` (+ `meeting_intelligence.answer`) | Live answers with sources during a call; flags out‑of‑scope. |

**AI features, not agents** — single LLM calls inside a feature (Livrables designer/generation,
automations' computer vision, knowledge indexing, contract extraction, the assistant helpers). The
Gouvernance IA page lists both groups separately (`governance.AGENT_KIND`) so caps apply to each.

## Workflows
`billing`, `risk`, `review` (billing + risk), **`copil`** (incidents → capacity → billing →
governance), **`onboarding`** (knowledge transfer + incidents + capacity), **`governance`**
(incidents + governance), **`capacity`**, **`estimation`**, `document`, `call`, and `ask` (free
question, read‑only, routed to the analysis agents). Gated kinds (`prebilling`, `document`,
`minutes`, `copil`, `staffing`) wait for human approval before they apply.

## Orchestration & human-in-the-loop
- A LangGraph **supervisor** plans, runs the needed agents in **dependency order** (`DEPENDENCIES`:
  risk after billing; governance after billing + risk), crosses their analyses with the documentation,
  and reaches an **approval interrupt** (checkpointed). The SPA shows the pending decision;
  approve/reject **resumes** the run.
- Rejections require a reason; approvals apply their effect (assignments, problem tickets, contract
  rules, indexed knowledge). Extracted contract rules are normalised before they are applied: profile labels
  become the platform codes (`Confirmé` → `confirmed`, `Architecte` → `architect`), currency symbols become ISO codes,
  UO codes and priorities are upper-cased — so the rate card matches the team members' profiles at pre-billing time. Full audit trail on every recommendation with sources + confidence.

## Deterministic vs generative
All client‑facing numbers (amounts, SLA %, loads) come from `analytics/`. The LLM extracts, reasons and
writes prose (risks, knowledge cards, minutes, COPIL narrative, copilot answers), always with a
fallback so the platform runs offline.

## Copilot specifics
Native OpenAI tool calling with a JSON‑protocol fallback (`LLM_TOOL_MODE=json`) for models without
tool calling, and an offline planner. Same tools are exposed to external assistants through the **MCP
server** (`mcp_server.py`, port 8765) under the same access rules.

## Shared "delivery brain" (project‑aware retrieval)
So the surfaces stop feeling like separate apps, they read one shared substrate scoped to the **whole
project**, not a single périmètre:
- `app/context.py` resolves a périmètre's project scope: `scope_perimeter_ids` (self + sibling
  périmètres under the same `DeliveryProject`), `project_team`, `latest_project_plan`.
- `knowledge.search(db, perimeter_id, …)` now accepts **a list of périmètre ids** — pass the whole
  project to search across all its périmètres. Each `Hit` carries its `perimeter_id`.
- The copilot `Toolbox` is **project‑aware**: `search_documents` covers the whole project (hits from a
  sibling are labelled with its name), and new tools expose data that used to be SQL‑only —
  `get_project` (manager, technologies, siblings, team), `get_plan_de_charge` (project workload plan:
  jours & coûts per resource), `get_staffing` (who's staffed across the project, with skills), and
  `get_deliverables`. The system prompt names the project + siblings.
- The **call assistant** grounds project‑wide too (`live_assistant.prepare` unions the project's
  périmètres), so it can answer from a sibling's docs and the project plan de charge.
- The **companion** is bridged to delivery (Phase B): `companion._delivery_context` adds a
  `<projet_courant>` snapshot (team, plan de charge) + `<livraison>` retrieval over the périmètres the
  user can see — scoped to what `user.can_see` allows, falling back to their whole visible portfolio.
  So one companion answers both personal ("summarize my morning") and delivery ("where's the Atlas
  plan de charge?") questions.
- **Ambient UI context** (Phase D): the app shell sets `Companion.scope` from the current route
  (`/p/:id` → perimeter, `/pr/:id` → project) and the chat request carries `perimeter_id`/`project_id`,
  so "this project" resolves without a dropdown. The dock shows a small scope chip.
- **Deeper orchestrated hand-off** (Phase E): `AgentContext` now carries `project_id` (set by the
  orchestrator), so the orchestrated agents are project-aware. The read‑only agents retrieve
  project‑wide — `doc_generator`, `estimation` and the live meeting answers (`meeting_intelligence`)
  search across the project's périmètres. And downstream agents consume upstream **structured `data`**,
  not just the summary string: `risk` now folds billing's actual figures/anomalies (`upstream["billing"]
  ["data"]`) into its reasoning. (`AgentOutput.to_state()` already propagated `data`.)

## Exports
`exports.py` produces the COPIL deck (`.pptx`), pre‑invoice workbook + justificatifs (`.xlsx`), and
minutes / onboarding pack (`.docx`). (Distinct from the newer method‑based **Livrables** generator —
see [deliverables.md](deliverables.md).)

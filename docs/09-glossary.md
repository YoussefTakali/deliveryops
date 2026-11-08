# 09 — Glossary

| Term | Meaning |
|---|---|
| **AMS / TMA** | Application Maintenance Services / Tierce Maintenance Applicative — ongoing maintenance & evolution of a client's applications. The core domain. |
| **Périmètre** | A delivery scope/project (client + applications + team + documents). The central business object; most features hang off a périmètre. |
| **COPIL** | *Comité de pilotage* — a steering‑committee review. The COPIL "support" (deck) covers: faits marquants, suivi de l'activité, plan d'action/décisions/risques, perspectives. Generated as a `.pptx`. |
| **Matrice de couverture** | A test coverage matrix crossing business requirements (exigences métier) with use cases/user stories, scoring **criticité** and preparing test effort. Generated as a `.xlsx`. |
| **Criticité** | Risk score (1–3) read from an Impact × Probabilité grid (Catastrophique/Grave/Modéré/Ennuyeux × Inévitable/Fréquent/Occasionnel/Rare). |
| **Exigence métier** | A business requirement (matrix columns). |
| **Cas d'usage / user story** | A usage scenario (matrix rows). |
| **Livrable** | A deliverable (COPIL, matrice, …) generated in the project's **Livrables** tab. |
| **Companion** | The personal Windows work companion: context‑aware chat + proactive help + agentic tasks. |
| **"Faire"** | The companion's agentic task flow: plan → clarifying questions → show exact commands → run with per‑step confirmation. |
| **Nudge / suggestion** | A proactive offer the companion makes (meeting prep, follow‑up, stuck help…); the user's accept/dismiss trains a per‑kind weight. |
| **Stuck detection** | Heuristic over recent window titles that spots the user struggling (e.g. repeated "download kafka") and offers to do it. |
| **Faithful replay** | Deterministic record‑and‑replay of desktop actions — re‑finds each control (UI‑Automation → image → AI) and verifies before acting, rather than replaying blind coordinates. |
| **Dataset run** | Replaying a recorded automation once per row of a table, with `{{row.Column}}` substituted. |
| **Ground truth (automation)** | Per‑click signals captured at record time: element identity, an image patch, coordinates. |
| **UI Automation (UIA)** | Windows accessibility API; used to find and **invoke** controls coordinate‑free. |
| **Orchestrator** | LangGraph supervisor that plans, runs agents in dependency order, and pauses at a human‑approval interrupt. |
| **Recommendation** | An agent output awaiting a human decision; approving it applies its effect. |
| **Perimeter isolation** | Data scoping by user/client/perimeter; out‑of‑scope access returns 404. |
| **MCP** | Model Context Protocol — the server exposing platform tools to external assistants. |
| **NIM** | NVIDIA Inference Microservices — the default hosted, OpenAI‑compatible LLM catalogue. |
| **RAG** | Retrieval‑Augmented Generation — hybrid search over embedded document chunks with citations. |
| **HITL** | Human‑in‑the‑loop — impactful actions block until a person decides. |

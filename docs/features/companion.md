# Feature — Work companion

A personal, Windows work companion: it knows the user's Microsoft + indexed data, knows what they're
doing right now (opt‑in), offers help proactively, learns their preferences, and can **do tasks**, not
just chat. Enterprise‑grade (privacy, admin, roles) but feels like an assistant.

- Backend: `backend/app/companion.py`, `backend/app/routes/companion.py` (`/api/companion/*`).
- Frontend: `frontend/src/app/companion.service.ts`, `frontend/src/app/pages/companion.ts`,
  `pages/admin-companion.ts`.
- Desktop: `desktop/src/activity.js` (activity), `background.js` (tray/background).
- Tables: `CompanionProfile/Memory/Suggestion/Message/Policy`, `ActivityEvent`.

## Context it uses
- **Memories** — inspectable plain‑text facts/preferences/people/projects (explicit "souviens‑toi…",
  LLM distillation every few turns, and feedback). The user can edit/pin/delete.
- **Activity** (opt‑in) — the foreground app + window title as "focus sessions", so it knows what the
  user is working on now. Feeds context and stuck detection.
- **Microsoft/agenda** — cached upcoming events (when a Graph token is present) for meeting nudges.
- **Indexed knowledge** — the user's mails/files via the RAG search (the personal `me-…` space).
- **Delivery data (Phase B)** — when the user has delivery access, the companion also reads the
  delivery side, scoped to what they can see: a `<projet_courant>` snapshot (project, team, plan de
  charge totals) plus `<livraison>` retrieval across the relevant périmètres (`_delivery_context`). It
  uses the **ambient scope** the app shell sends — `perimeter_id`/`project_id` of the page the user is
  on (`Companion.scope`, set from the route) — falling back to their whole visible portfolio. So one
  companion answers both "résume ma matinée" and "où en est le plan de charge d'Atlas ?", always
  citing sources, and never leaks a client the user can't see.

## Recommended options & debugging with the user
- Every set of options the companion offers marks exactly ONE as « (recommandé) » — in chat (system
  prompt) and in the "Faire" task questions (`suggest_answers` returns `recommended`; the UI badges and
  pre-selects it).
- **« Déboguer avec l'IA »**: any error in the app can be handed to the companion
  (`Companion.debug(context, error)` → the dock opens and sends a `[DÉBOGAGE]` message; on the
  /companion page the page chat does). The companion answers with the likely cause, 2–3 numbered
  fixes with one recommended, and at most one question. A failed "Faire" task passes its log tail too.
- A governance refusal (AI blocked, quota, agent cap) is shown as its real reason, never as a fake answer.

## Proactive help (nudges)
Deterministic **triggers** in `_candidates` (cheap, auditable), the LLM only used to *talk*:
`stuck_help` (highest), `meeting_prep`, `meeting_followup`, `daily_brief`, `context_help`,
`deep_work_summary`, `automate_repetition`, `focus_break`, `day_recap`. Each has a base priority and
cool‑down; dedupe fingerprints, quiet hours and an org hourly cap apply. Accept (+0.15) / dismiss
(−0.25) / never (0) move a per‑kind weight; weights < 0.3 suppress that kind.

Suggestions surface on the ~60s `/state` poll as offer cards and native toasts (even when the window
is hidden in the tray). **Requires activity tracking ON + proactive help on + outside quiet hours.**

### Stuck detection (`_detect_stuck`)
Reads the last ~20 min of window titles and fires when the user is struggling: repeated
install/download attempts, error signals, or circling the same task. It extracts the **subject
generically** (works for any software, e.g. "download kafka" → "installer kafka"), fires on ≥2 clear
install/download searches about one subject, and names the task. Accepting the offer launches the
agentic **"Faire"** flow (action `type:"task"`).

## Doing tasks — "Faire" (agentic)
The companion executes real tasks via the safe automations pipeline. Model chosen: **confirm each
risky step, inline in the chat**.

1. `POST /api/companion/task` plans the task with the automation **compiler**. If under‑specified it
   returns clarifying **questions** (with clickable options via `suggest_answers`); the UI asks them
   **one at a time** (stepper: option cards + free‑text "Autre").
2. Once answered, it returns a concrete **plan**: a draft `Automation` (`source=companion_task`) with a
   `flow.confirm` gate before every side‑effecting step and the **exact PowerShell** to approve —
   nothing pre‑approved.
3. The user approves the scripts and runs it through `AutomationRunner`, with per‑step confirmation and
   a live log. It verifies the result (e.g. `python --version`).

Guardrails (`TASK_GUIDANCE`): real Windows steps, winget/official sources, exact commands shown, a
verify step, never credentials/payment/security settings. Execution needs the desktop app (local
PowerShell) and requires `automation.use`.

## Background & tray
Closing the window hides to the notification‑area tray (default on) so the companion keeps
watching/suggesting; optional launch at Windows login. Toggles live in the companion preferences and
are wired via the desktop bridge (`getBackground/setBackground`).

## Administration & governance
`/api/companion/admin/*` (perm `companion.admin`): org **policy** (tracking allowed, retention,
title capture, proactive cap, memory learning), **aggregate metrics** and **per‑user adoption counts**
(never titles/memories/messages), and per‑user erasure. Everything is audited.

## Tested
`backend/tests/test_companion.py` covers opt‑in tracking + sensitive‑window filtering, feedback
adaptation, memory dedupe/ownership, chat + explicit memory, policy governance/aggregates, **stuck
detection** (incl. the generic "download kafka" case), and the **task planner** (questions → plan with
confirm gates + exact scripts, nothing pre‑approved).

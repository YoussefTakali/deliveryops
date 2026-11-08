# Feature — Enterprise AI governance & supervision

Admins control **who uses the platform, who uses the AI, and how much**. Every change is audited, and
the audit trail itself is searchable.

- Backend: `backend/app/governance.py` (checks, cached usage), `backend/app/routes/governance.py`
  (`/api/governance/*`), the LLM choke point in `backend/app/llm.py` (`_guard`, `LLMBlocked`),
  per-user attribution in `backend/app/telemetry.py`, app-block + `require_ai` in `backend/app/auth.py`.
- Frontend: `pages/admin-governance.ts` (**Supervision › Gouvernance IA**), `pages/audit.ts`,
  `pages/observability.ts`.
- Tables (all new, no ALTER): `GovernancePolicy` (single row `org`), `UserGovernance` (keyed by
  username — works in every auth mode, dev personas included), `UsageLedger` (one row per LLM call:
  user, agent, tokens, status). Migration `d0e1f2a3b4c5`.
- Permission: **`governance.manage`** (granted to admin + director by default, backfilled once into
  existing deployments).

## Controls
| Control | Scope | Effect |
|---|---|---|
| AI switch | organisation | Off → every agent degrades to its deterministic fallback; no tokens consumed. |
| Suspend access (`app_blocked`) | user | Every API call answers **403** — the UI shows an « Accès suspendu » screen. Takes effect within seconds (5 s cache), in every auth mode. An admin can't suspend themselves. |
| Block AI (`ai_blocked`) | user | The user keeps the app; AI calls are refused with a clear reason. |
| Daily token quota | org default + per-user override | Tokens since UTC midnight ≥ quota → refused (429). `0` = unlimited; a per-user value (even 0) overrides the default; "reset" returns to the default. |
| Daily cap per agent | agent (copilot, companion, call assistant, livrables, billing, …) | Tokens of that agent today (all users) ≥ cap → that agent is refused; others keep working. |

## Where it's enforced
1. **Every model call** goes through `llm.chat()` or `llm.stream_text()`, and both call `_guard`. A
   refusal raises **`LLMBlocked`, a subclass of `openai.APIError`** — so every existing caller that
   already copes with API errors (the `structured`/`text` fallbacks, the copilot, automations) degrades
   gracefully without any change. `stream_text` turns it into `LLMUnavailable(blocked=True)` and the
   companion shows the reason instead of a fake answer. Refusals are recorded with status `blocked`.
2. **Interactive endpoints** answer early with a clear **403 / 429**: `auth.require_ai` on copilot,
   personal ask/document, live call assistant, custom-livrable design/prepare; `consume_llm_quota`
   (companion chat/brief/task) runs the same check. Workflow runs are deliberately not blocked at the
   route: their deterministic parts (pre-billing, KPIs) still work, only their AI steps fall back.
3. **Every authenticated request** checks `app_blocked` (`governance.assert_app_access` in
   `current_user`).

Checks **fail open** on database errors — governance must never be the reason the platform stops.

## Attribution (who consumed what)
`LLMCall` has no user column, so a new `UsageLedger` row is written for every call. The user comes
from a per-request holder installed by `RequestContextMiddleware` and filled by `current_user` (a
shared mutable object, so it reaches the endpoint thread and streaming bodies — a plain ContextVar set
in the sync auth dependency would not). Background agent runs are attributed to
`AgentRun.requested_by`. Calls made outside an agent block are mapped to an agent from their purpose
(`governance.agent_of`).

## Supervision views
- **Gouvernance IA**: policy, per-agent caps with today's usage bars, users table with search (today /
  7 days / quota / AI / access / note), consumption per day and per agent.
- **Observabilité IA**: now enforced by `observability.view`, plus **per user** and **per model** tables.
- **Journal d'audit**: now enforced by `audit.view`; **full-text search** (actor, action, object,
  details), action/actor filters (`/api/audit/facets`), date range, pagination (`X-Total-Count`), CSV
  export — and org-level admin actions (governance, users, roles, policies: `perimeter_id` NULL) are now
  visible to admin/all-client users (they were silently hidden before).
- The **Supervision** section moved right under Pilotage in the sidebar.

## API
| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/api/governance/policy` | AI switch, default daily quota, agent caps (merge; 0 removes a cap) |
| GET | `/api/governance/users?q=` | users (directory + anyone governed/active today) with usage and rules |
| PUT | `/api/governance/users/{username}` | `app_blocked`, `ai_blocked`, `daily_token_quota`, `reset_quota`, `note` |
| GET | `/api/governance/usage?days=` | tokens by user / agent / day |
| GET | `/api/audit?q=&actor=&action=&since=&until=&limit=&offset=` | searchable audit (header `X-Total-Count`) |
| GET | `/api/audit/facets` | distinct actions and actors |

## Tested
`backend/tests/test_governance.py`: admin-only access, suspension (403, self-lock refused), AI block vs
app block, per-user quota + org default + override, agent caps and the kill switch, the `_guard` choke
point raising an `openai.APIError`, per-user/per-agent attribution in usage and observability, and the
searchable audit (search, facets, `audit.view` enforced).

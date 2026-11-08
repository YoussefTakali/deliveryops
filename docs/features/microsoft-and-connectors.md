# Feature — Microsoft 365 & connectors

Bring the user's real work data in: Microsoft 365 (mail, calendar, files, Teams) via Graph, plus
delivery‑system connectors (Jira, ServiceNow, demo).

## Microsoft 365 (Entra ID + Graph)
- Backend: `backend/app/microsoft_graph.py`, `backend/app/routes/microsoft.py`,
  `backend/app/routes/microsoft_admin.py`. Frontend: `frontend/src/app/microsoft-auth.ts` (MSAL),
  `frontend/src/app/pages/microsoft.ts`.
- **Sign‑in** (`auth_mode=microsoft`): MSAL in the SPA — `ssoSilent` picks up the Windows account when
  possible, else a one‑click redirect. Only `User.Read` is requested at sign‑in; data scopes are
  acquired incrementally so a restrictive tenant still signs in.
- **Scopes** (`config.py`): basic (self‑consentable) `User.Read Mail.Read Calendars.Read Tasks.Read
  Presence.Read`; admin‑gated `Files.Read.All Sites.Read.All Chat.Read Team.ReadBasic.All`;
  automation `Mail.Send` (incremental, only when an automation sends mail). A tenant‑admin consent URL
  is offered when needed; restrictive tenants degrade gracefully (fewer sources) instead of blocking.
- **Token relay**: the SPA sends the Graph access token as `X‑Graph‑Token`; the backend relays it to
  Graph, so the browser never sends it anywhere but our origin and Microsoft. Graph calls are bounded
  (timeout) and wrapped so a failure never breaks a request (e.g. the companion's cached agenda).
- **Used by**: companion (live mail/agenda + meeting nudges), knowledge search, call assistant (Teams),
  automations that send mail.

## Connectors (Jira / ServiceNow / demo)
- Backend: `backend/app/connectors.py`, `backend/app/routes/connectors.py`, table `Connector`.
- Pull tickets/changes into a périmètre; a **demo feed** works with no external system.
- **Secrets are referenced by environment‑variable name only — never stored** in the database.
- The proactive **watch** (`watch.py`) can sync connectors on a schedule and run deterministic checks
  (SLA breaches, KPI drift, incident spikes, overload, régie run‑rate, missing timesheets, stale
  approvals) → deduplicated notifications, with Teams cards for critical ones (`notify.py`,
  `TEAMS_WEBHOOK_URL`).

## Tested
`backend/tests/test_microsoft.py` (config/scopes, Graph relay with a mock transport). Note: one
pre‑existing config‑default test failure depends on whether `MICROSOFT_*` is set in the environment.

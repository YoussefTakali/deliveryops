# 08 — Security & privacy

## Authentication modes (`AUTH_MODE`, `backend/app/auth.py`)
| Mode | Use | How the user is resolved |
|---|---|---|
| `native` | Self‑hosted, no external IdP | Email/password → RS256/HS JWT signed with `AUTH_SECRET`; `AuthUser` + `RefreshToken`. |
| `keycloak` | Enterprise OIDC | Keycloak OIDC/PKCE; RS256 JWT validated (signature, issuer, audience, expiry). In‑app user admin via Keycloak Admin API. |
| `microsoft` | Entra ID SSO | MSAL in the SPA; the API validates the sign‑in and relays a Graph token (`X‑Graph‑Token`) for live data. Roles via `MicrosoftUser`. |
| `dev` | Local dev/tests only | Fixed `DEV_USERS` (sarah=director, karim/lea=delivery_manager, tom=consultant). Blocked in production. |

All modes converge on a `User` with `permissions` and client/perimeter access. Endpoints use
`require_permission("...")` and are owner/client/perimeter‑scoped — they return **404, not 403**, for
out‑of‑scope objects so nothing leaks across tenants.

## Authorization (RBAC + groups)
- **Dynamic roles → permissions**: an admin creates roles, grants permissions, assigns people
  (`Role`, `RolePermission`; `rbac.py`, `permissions.py`). No fixed hierarchy.
- **Nested groups** (Linux‑directory‑like) with **Unix‑style permission bits** (owner/group/others ×
  read=4/write=2/admin=1); `owner` is the user's username/email across auth modes. Group nesting gives
  managers oversight of descendants. See [features/team-and-permissions.md](features/team-and-permissions.md).

## Companion privacy (regulated‑buyer posture)
- **Activity tracking is opt‑in per user AND allowed per org policy** (`CompanionPolicy`). Off by
  default. Only the foreground app + window title are read — never keystrokes or screen content.
- Sensitive windows are dropped before storage (password managers, banking, private browsing, payslips…);
  titles can be disabled org‑wide (`title_capture=app_only`); data expires (retention); the user can
  export or erase everything.
- **Admin monitoring is aggregate‑only** — an administrator never sees window titles, memories, or the
  conversation. Consent/policy/erasure are audited.
- Learning is **inspectable**: memories are plain text the user can edit/pin/delete; nudge preferences
  are weights moved by the user's own accept/dismiss clicks — no opaque fine‑tuning.

## Automation & task safety
- PowerShell runs **only the exact, hash‑approved script** the user saw (`approved_script_hashes`).
- Companion agentic tasks ("Faire") **confirm each side‑effecting step**, show the exact commands,
  never enter credentials/payment, never change security settings, and prefer official sources.
- Automations execute on the client; screenshots for vision **never leave the PC** — only text labels,
  positions, and tiny icon crops are sent for the AI decision.

## Web/platform hardening (`middleware.py`, `config.py`, `db.py`)
- Rate limiting, security headers, structured JSON request logging with request IDs, LLM quota.
- **Production guard**: with `ENVIRONMENT=production` the app refuses to start if misconfigured — dev
  auth, SQLite, demo seed data, wildcard CORS, or an unmigrated schema.
- Non‑root multi‑stage Docker images, health checks.
- Connector secrets are referenced by **env‑var name only**, never stored in the DB.

## Audit
`AuditLog` records every impactful action (approvals, policy/consent changes, erasure, deliverable
generation, template edits…) with actor, target and timestamp.

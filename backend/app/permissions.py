"""RBAC permission catalog and default role presets.

A *permission* is a named functionality (e.g. `recommendation.decide`). Every authenticated user
carries a set of permission codes; the frontend hides what the user can't do and the API enforces
it with `require_permission(...)`. Which permissions a role grants is stored in the database
(`role_permissions`) and editable by an admin — this catalog only defines the fixed set of codes
that exist and sensible defaults to seed with.
"""

# code -> (label, category). Categories group the permission matrix in the admin UI.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "portfolio.view": ("Voir le portefeuille", "Consultation"),
    "perimeter.view": ("Consulter les périmètres", "Consultation"),
    "billing.view": ("Voir la facturation", "Consultation"),
    "capacity.view": ("Voir la capacité", "Consultation"),
    "incidents.view": ("Voir incidents & chiffrage", "Consultation"),
    "observability.view": ("Voir l'observabilité IA", "Consultation"),
    "audit.view": ("Voir le journal d'audit", "Consultation"),

    # One toggle per public agent + the orchestrator, so an admin grants each role exactly the
    # agent functionalities it should have.
    "orchestrator.run": ("Lancer l'orchestrateur (workflows)", "Agents"),
    "agent.doc_generator": ("Agent — Générateur de documents", "Agents"),
    "agent.call_assistant": ("Agent — Assistant d'appel", "Agents"),
    "agent.billing": ("Agent — Facturation", "Agents"),
    "agent.risk": ("Agent — Analyse des risques", "Agents"),
    "copilot.use": ("Copilot / question libre", "Agents"),
    "recommendation.decide": ("Valider / rejeter les recommandations", "Agents"),
    "skills.manage": ("Entraîner les agents (compétences)", "Agents"),

    "perimeter.create": ("Créer des périmètres", "Gestion"),
    "perimeter.manage": ("Gérer un périmètre (contrat, connecteurs, imports)", "Gestion"),
    "knowledge.manage": ("Gérer la base de connaissance (documents)", "Gestion"),

    "microsoft.use": ("Espace Microsoft 365", "Intégrations"),
    "localfiles.use": ("Fichiers du PC (app bureau)", "Intégrations"),

    "automation.use": ("Automatisations", "Outils"),
    "techwatch.use": ("Veille technologique personnalisée", "Outils"),

    # The work companion. `companion.activity` is separate so an organisation can offer the companion
    # without ever allowing activity capture for some roles; `companion.admin` governs the org policy
    # and sees aggregate monitoring only.
    "companion.use": ("Compagnon de travail", "Compagnon"),
    "companion.activity": ("Compagnon — suivi d'activité (opt-in utilisateur)", "Compagnon"),
    "companion.admin": ("Compagnon — politique & supervision", "Administration"),

    # Groups & project management (Unix-style permissions on projects/tasks, on top of the RBAC).
    "team.view": ("Équipe & projets — voir et participer", "Équipe"),
    "team.manage": ("Équipe & projets — administrer groupes et tout le contenu", "Administration"),

    "users.manage": ("Administrer les utilisateurs", "Administration"),
    # Enterprise AI governance: suspend a user, block their AI use, daily token quotas, per-agent caps.
    "governance.manage": ("Gouvernance IA — blocages, quotas, plafonds par agent", "Administration"),
    # Company rules every agent and user must respect (confidentiality, approved software…).
    "policy.manage": ("Règles & conformité — gérer les règles de l'entreprise", "Administration"),
    # The company catalogue of livrables (the methodology every project can pick from).
    "deliverables.catalog": ("Livrables — gérer le catalogue de l'entreprise", "Administration"),
    "roles.manage": ("Administrer les rôles & permissions", "Administration"),
}

ALL_PERMISSIONS = frozenset(PERMISSIONS)

# Read-only viewing bundle reused by the presets below.
_VIEW = {"portfolio.view", "perimeter.view", "billing.view", "capacity.view", "incidents.view"}
# Every agent functionality + the orchestrator + copilot.
_ALL_AGENTS = {"orchestrator.run", "agent.doc_generator", "agent.call_assistant", "agent.billing", "agent.risk", "copilot.use"}

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    # Platform administrator: everything, including editing what other roles can do.
    "admin": set(ALL_PERMISSIONS),
    # Delivery director: full business scope + user administration, but not role/permission editing.
    "director": set(ALL_PERMISSIONS) - {"roles.manage"},
    # Delivery manager: runs the platform for their clients; every agent, can decide and manage.
    "delivery_manager": _VIEW | _ALL_AGENTS | {
        "observability.view", "audit.view", "recommendation.decide", "skills.manage",
        "perimeter.manage", "knowledge.manage", "microsoft.use", "localfiles.use", "automation.use",
        "companion.use", "companion.activity", "team.view", "team.manage", "techwatch.use",
    },
    # Consultant: read-only, plus the Copilot, the doc generator and the call assistant.
    "consultant": _VIEW | {"copilot.use", "agent.doc_generator", "agent.call_assistant", "microsoft.use", "localfiles.use", "automation.use",
                           "companion.use", "companion.activity", "team.view", "techwatch.use"},
    # Default for anyone who signs in (e.g. via Microsoft) before an admin has assigned them a real
    # role: zero permissions. Deliberately not a viewer default — an unknown Microsoft account
    # succeeding at sign-in must not imply any access to the platform's data.
    "unassigned": set(),
}

# System roles cannot be deleted. The `admin` role's permissions are locked (can't be edited away,
# to prevent locking every admin out); other system roles are editable.
SYSTEM_ROLES = ("admin", "director", "delivery_manager", "consultant", "unassigned")
ROLE_LABELS = {
    "admin": "Administrateur", "director": "Directeur delivery",
    "delivery_manager": "Delivery Manager", "consultant": "Consultant",
    "unassigned": "En attente d'assignation",
}
LOCKED_ROLES = ("admin",)

# Permissions introduced after the first release. `ensure_roles` only seeds a role's defaults when the
# role is first created, so without this an existing deployment's roles would never receive a new
# permission. Each code listed here is granted ONCE to the system roles whose default preset includes
# it (recorded in the audit log, so an admin who later removes it doesn't get it forced back).
BACKFILLED_PERMISSIONS = ("automation.use", "companion.use", "companion.activity", "companion.admin", "team.view", "team.manage", "governance.manage", "deliverables.catalog", "techwatch.use", "policy.manage")


def default_permissions_for(role: str) -> set[str]:
    return set(DEFAULT_ROLE_PERMISSIONS.get(role, _VIEW))

"""Domain model. Every business row is scoped to a perimeter, and every perimeter to a client
(tenant). Access checks in app/auth.py rely on that chain for client isolation."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.config import get_settings
from app.db import Base

settings = get_settings()


def _id() -> str:
    return uuid.uuid4().hex[:12]


# Pseudo-client holding every user's personal knowledge space ("Mon espace": indexed Microsoft 365
# data and PC files, see app/assistant.py). Stored as perimeters for the RAG machinery, but never a
# delivery périmètre: only its owner reaches it, through /api/assistant (see User.can_see).
PERSONAL_CLIENT = "personal"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@event.listens_for(Base, "init", propagate=True)
def _assign_id(target, args, kwargs):
    """Give string primary keys their value at construction, so ids can be referenced before flush."""
    column = target.__table__.c.get("id")
    if column is not None and isinstance(column.type, String) and not kwargs.get("id"):
        target.id = _id()


class Embedding(TypeDecorator):
    """pgvector on PostgreSQL, JSON list elsewhere (SQLite for local dev and tests)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(settings.embedding_dim))
        return dialect.type_descriptor(JSON())

    def process_result_value(self, value, dialect):
        return None if value is None else list(value)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(100), default="")
    perimeters: Mapped[list["Perimeter"]] = relationship(back_populates="client")


class Perimeter(Base):
    __tablename__ = "perimeters"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    # A périmètre sits under a delivery project (a manager runs one or more). Nullable so existing
    # rows and per-user personal spaces stay valid; NEW column on an existing table → needs a migration.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("delivery_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Structured contract: billing model, rates, UO catalog, SLA targets, penalties, scope
    contract: Mapped[dict] = mapped_column(JSON, default=dict)
    client: Mapped[Client] = relationship(back_populates="perimeters")
    project: Mapped["DeliveryProject | None"] = relationship(back_populates="perimeters")


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    technology: Mapped[str] = mapped_column(String(200), default="")
    criticality: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|critical
    business_owner: Mapped[str] = mapped_column(String(200), default="")
    users_count: Mapped[int] = mapped_column(Integer, default=0)


class Dependency(Base):
    __tablename__ = "dependencies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    kind: Mapped[str] = mapped_column(String(50), default="api")  # api|batch|db|file|mq
    description: Mapped[str] = mapped_column(Text, default="")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str] = mapped_column(String(40))  # transfer|dat|dex|contract|architecture|minutes|living
    content: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Embedding, nullable=True)


class TeamMember(Base):
    __tablename__ = "team_members"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(String(50))  # matches contract rate card keys
    skills: Mapped[list] = mapped_column(JSON, default=list)
    weekly_capacity_h: Mapped[float] = mapped_column(Float, default=35.0)
    absences: Mapped[list] = mapped_column(JSON, default=list)  # [{"start": iso, "end": iso}]


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    ref: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # incident|request|change|problem
    priority: Mapped[str] = mapped_column(String(5))  # P1..P4
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|resolved|closed
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("team_members.id"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened: Mapped[int] = mapped_column(Integer, default=0)
    resolution: Mapped[str] = mapped_column(Text, default="")
    estimate_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    failed_change: Mapped[bool] = mapped_column(default=False)


class Timesheet(Base):
    __tablename__ = "timesheets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("team_members.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    activity_code: Mapped[str] = mapped_column(String(40))  # RUN|MCO|EVOL|PROJ|INTERNAL|...
    ticket_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hours: Mapped[float] = mapped_column(Float)
    comment: Mapped[str] = mapped_column(Text, default="")


class BacklogItem(Base):
    __tablename__ = "backlog_items"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    required_skill: Mapped[str] = mapped_column(String(100))
    estimate_h: Mapped[float] = mapped_column(Float)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 = highest
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("team_members.id"), nullable=True)


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    transcript: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="live")  # live|ended
    minutes: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    workflow: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="running")  # running|awaiting_approval|completed|failed
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    steps: Mapped[list] = mapped_column(JSON, default=list)  # orchestration trace
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Recommendation(Base):
    """Anything an agent proposes. High-impact items block the workflow until a human decides."""

    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(50))  # risk|staffing|billing|action|scope|problem|...
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # [{"type","ref","label","excerpt"}]
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|info
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    perimeter_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entity: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class LLMCall(Base):
    """One model call, for cost/latency observability and per-agent attribution."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    perimeter_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(50), default="")
    purpose: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(60))  # "offline" when the deterministic fallback answered
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|fallback|error|refusal


class Conversation(Base):
    """Copilot chat thread. `messages` holds the display transcript incl. tool-call traces."""

    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    username: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300), default="Nouvelle conversation")
    messages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    # Nullable: a perimeter-scoped notification (watch_alert, etc.) has one; a global/admin-scoped
    # notification (e.g. user_pending_approval) has none and is instead routed by `kind` + permission.
    perimeter_id: Mapped[str | None] = mapped_column(ForeignKey("perimeters.id"), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    kind: Mapped[str] = mapped_column(String(50))  # watch_alert|approval_required|run_completed|sync|user_pending_approval
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    fingerprint: Mapped[str] = mapped_column(String(200), default="", index=True)  # dedupe key for repeated alerts
    read_by: Mapped[list] = mapped_column(JSON, default=list)
    # Who a global notification (no perimeter) is for: one person (recipient), holders of a permission
    # (audience, e.g. "governance.manage"), or — both empty, the historical default — user admins.
    audience: Mapped[str] = mapped_column(String(60), default="")
    recipient: Mapped[str] = mapped_column(String(200), default="", index=True)


class Connector(Base):
    """External data source for a perimeter. Secrets are never stored: `config.token_env` names
    the environment variable holding the credential."""

    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # jira|servicenow|file
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="never")  # never|ok|error
    last_message: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- native auth / RBAC

from sqlalchemy import UniqueConstraint  # noqa: E402


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    is_system: Mapped[bool] = mapped_column(default=False)  # system roles can't be deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role", cascade="all, delete-orphan")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_code", name="uq_role_permission"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    permission_code: Mapped[str] = mapped_column(String(60))
    role: Mapped[Role] = relationship(back_populates="permissions")


class AuthUser(Base):
    """A person who can sign in. Distinct from TeamMember (a resource on a perimeter's team)."""

    __tablename__ = "auth_users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), index=True)
    clients: Mapped[list] = mapped_column(JSON, default=list)  # ["*"] = all clients
    active: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Brute-force protection: consecutive failed sign-ins, and the lock they triggered.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Two-factor authentication (TOTP, app/totp.py). The secret is encrypted (app/secret_box.py);
    # recovery codes are stored hashed; mfa_last_step blocks replaying a code already used.
    mfa_enabled: Mapped[bool] = mapped_column(default=False)
    mfa_secret_enc: Mapped[str] = mapped_column(Text, default="")
    mfa_recovery: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mfa_last_step: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[Role] = relationship()


class RefreshToken(Base):
    """One signed-in browser/device. Rotated on every refresh; the new row inherits the device info."""
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    signed_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # start of this device's session


class ExternalIdentity(Base):
    """A Microsoft or Google identity that can sign in to an AuthUser (one account, several methods).
    Keyed by the provider's stable, non-reassignable id (Microsoft `oid`, Google `sub`), never by e-mail."""
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_external_identity"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # microsoft | google
    subject: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    """Single-use "forgot password" link. Only the hash is stored; expires quickly."""
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentSkill(Base):
    """A learned instruction for one agent. Seed skills ship as files (app/agents/skills/*.md);
    these are the runtime additions — captured from human feedback or added by a user "training" the
    agent — and are injected into that agent's prompt alongside the file baseline."""

    __tablename__ = "agent_skills"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    agent: Mapped[str] = mapped_column(String(40), index=True)  # doc_generator|meeting_intelligence|billing|risk|copilot
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="user")  # user|feedback|auto
    perimeter_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # optional scope; null = global
    created_by: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MicrosoftUser(Base):
    """A person who has signed in via Microsoft (auth_mode=microsoft). Distinct from AuthUser
    (native, password-based): identity is verified by Microsoft, not by us, so there is no password
    here — only the role/client scope an admin assigns after the fact. The very first person ever to
    sign in becomes `admin` (bootstrap); everyone after starts at `unassigned` (zero permissions)
    until an admin grants them a real role — see app.rbac.ensure_microsoft_user."""

    __tablename__ = "microsoft_users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(200), default="")  # Entra object id, for audit/reference
    name: Mapped[str] = mapped_column(String(200), default="")
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), index=True)
    clients: Mapped[list] = mapped_column(JSON, default=list)  # ["*"] = all clients
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    role: Mapped[Role] = relationship()


# ---------------------------------------------------------------- automations

from sqlalchemy import LargeBinary  # noqa: E402


class Automation(Base):
    """A user's recorded/described repetitive task, compiled into a reviewable workflow of semantic
    steps (see app/automations/catalog.py). Private to its owner (user.username)."""

    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|active|paused
    trigger: Mapped[dict] = mapped_column(JSON, default=dict)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)  # key -> {label, type: file|text, value}
    require_confirmation: Mapped[bool] = mapped_column(default=True)
    approved_script_hashes: Mapped[list] = mapped_column(JSON, default=list)  # sha256 hex of approved local.powershell scripts
    source: Mapped[dict] = mapped_column(JSON, default=dict)  # {kind: recording|prompt, recording_id?, prompt?}
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    mail_trigger_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # schedule|mail|manual|test
    dry_run: Mapped[bool] = mapped_column(default=False)
    # awaiting_confirmation|running|succeeded|failed|cancelled|skipped
    status: Mapped[str] = mapped_column(String(30), default="running")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[list] = mapped_column(JSON, default=list)  # [{ts, step_id, level, message, output_summary?}]
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutomationFile(Base):
    """Bytes moved through an automation: templates the user uploads (automation_id set), files
    produced or fetched during a run (run_id set), recording keyframes (neither)."""

    __tablename__ = "automation_files"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    automation_id: Mapped[str | None] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("automation_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)  # deferred: listing metadata never loads the bytes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AutomationRecording(Base):
    __tablename__ = "automation_recordings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    events: Mapped[list] = mapped_column(JSON, default=list)
    narration: Mapped[list] = mapped_column(JSON, default=list)  # [{t, text}]
    keyframes: Mapped[list] = mapped_column(JSON, default=list)  # [{t, file_id, window_title, process}]
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DeliverableTemplate(Base):
    """Per-company overrides for a built-in deliverable template (COPIL, matrice de couverture…).
    Built-in defaults live in app/deliverables/templates.py; this stores only the tweaks (title,
    questions, scales, grid) merged on top. One row per template key (org-wide)."""
    __tablename__ = "deliverable_templates"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


# ---------------------------------------------------------------- companion (app/companion.py)
# The work companion: a per-user profile it adapts to, the durable things it has learned, what the
# user is doing on their PC (opt-in, policy-governed), the help it proactively offered and how the
# user reacted (the learning signal), and the conversation. Everything is owner-scoped like the
# automations; an org-wide policy row governs what may be collected at all.
from sqlalchemy import Boolean as _Boolean  # noqa: E402


class CompanionProfile(Base):
    __tablename__ = "companion_profiles"
    owner: Mapped[str] = mapped_column(String(200), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    job_role: Mapped[str] = mapped_column(String(40), default="employee")  # employee|manager|hr|consultant|executive|assistant
    tone: Mapped[str] = mapped_column(String(20), default="warm")  # warm|neutral|direct
    language: Mapped[str] = mapped_column(String(8), default="")  # "" = follow the user
    quiet_start: Mapped[str] = mapped_column(String(5), default="")  # "HH:MM" local; no nudges inside the window
    quiet_end: Mapped[str] = mapped_column(String(5), default="")
    tracking_enabled: Mapped[bool] = mapped_column(_Boolean, default=False)  # explicit opt-in, never default on
    proactive_enabled: Mapped[bool] = mapped_column(_Boolean, default=True)
    # Opt-in: let people who share a group with you (e.g. your manager) see your live work focus.
    share_work: Mapped[bool] = mapped_column(_Boolean, default=False)
    onboarded: Mapped[bool] = mapped_column(_Boolean, default=False)
    # Learned: how much the user values each kind of nudge (1.0 neutral, 0 = never again).
    nudge_weights: Mapped[dict] = mapped_column(JSON, default=dict)
    agenda: Mapped[list] = mapped_column(JSON, default=list)  # cached upcoming events [{subject,start,end,attendees,join_url}]
    agenda_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messages_since_distill: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CompanionMemory(Base):
    __tablename__ = "companion_memories"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="preference")  # preference|fact|routine|person|project
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user (told explicitly) | learned (distilled) | feedback
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    pinned: Mapped[bool] = mapped_column(_Boolean, default=False)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivityEvent(Base):
    """One focus session: the user stayed on `app`/`title` from started_at for `seconds`."""
    __tablename__ = "companion_activity"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    app: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(300), default="")  # subject to the org policy (may be blank)
    category: Mapped[str] = mapped_column(String(30), default="other", index=True)


class CompanionSuggestion(Base):
    __tablename__ = "companion_suggestions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[dict] = mapped_column(JSON, default=dict)  # {type: chat|link|brief, prompt?|link?}
    fingerprint: Mapped[str] = mapped_column(String(200), default="", index=True)  # dedupe: never offer the same thing twice
    score: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|accepted|dismissed|snoozed|never|expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompanionMessage(Base):
    __tablename__ = "companion_messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    role: Mapped[str] = mapped_column(String(12))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)


class CompanionPolicy(Base):
    """Org-wide governance of the companion (single row, id='org'). Edited by `companion.admin`."""
    __tablename__ = "companion_policy"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="org")
    enabled: Mapped[bool] = mapped_column(_Boolean, default=True)
    activity_tracking_allowed: Mapped[bool] = mapped_column(_Boolean, default=True)
    title_capture: Mapped[str] = mapped_column(String(12), default="full")  # full|app_only
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    excluded_apps: Mapped[list] = mapped_column(JSON, default=list)  # process names never recorded
    excluded_keywords: Mapped[list] = mapped_column(JSON, default=list)  # window-title keywords never recorded
    proactive_allowed: Mapped[bool] = mapped_column(_Boolean, default=True)
    max_nudges_per_hour: Mapped[int] = mapped_column(Integer, default=4)
    memory_learning_allowed: Mapped[bool] = mapped_column(_Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(200), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)



# ---------------------------------------------------------------- groups & project management (app/team.py)
# Access is modelled on a Unix filesystem, on top of the dynamic role/permission RBAC:
#   - GROUPS are directory-like nodes that nest (parent_id). A user is a member of zero or more
#     groups; membership in a group also grants access to everything UNDER it (descendants), so a
#     top-level group acts like a manager's scope over sub-teams.
#   - Every resource (project, task) carries an OWNER (a user), a GROUP, and a Unix-style MODE:
#     three triads (owner / group / others) of read(4) + write(2) + admin(1) bits. `team.py` resolves
#     who may read/write/admin a resource from ownership, effective group membership and the "others"
#     triad — plus a global override for admins (the `team.manage` permission).
# Everything is keyed by `owner` (username/email) so it works across auth modes.

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    color: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    member_owner: Mapped[str] = mapped_column(String(200), index=True)
    # A member's standing in the group; "owner" (maintainer) may admin the group and its resources.
    group_role: Mapped[str] = mapped_column(String(20), default="member")  # owner|member
    title: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|paused|done|archived
    owner_user: Mapped[str] = mapped_column(String(200), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    mode: Mapped[int] = mapped_column(Integer, default=0o770)  # rwx owner / rwx group / --- others
    color: Mapped[str] = mapped_column(String(16), default="")
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WorkItem(Base):
    """A task on the board. Inherits its project's group/mode by default; can be assigned to anyone."""
    __tablename__ = "work_items"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="todo", index=True)  # todo|doing|blocked|done
    priority: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high|urgent
    owner_user: Mapped[str] = mapped_column(String(200), index=True)  # creator/owner
    assignee_owner: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    mode: Mapped[int] = mapped_column(Integer, default=0o770)
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    order: Mapped[float] = mapped_column(Float, default=0.0)  # position within its status column
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GroupActivity(Base):
    """Human-readable feed of board changes, so anyone with read access can follow progress."""
    __tablename__ = "group_activity"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    verb: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(400))
    ref_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


# ---------------------------------------------------------------- competences, staffing & generated deliverables
# Members are users: the competence matrix is per user (global), a périmètre staffs members from it.

class UserSkill(Base):
    """One skill of one user (the competence matrix). level 1..5; category: technology|functional|language|method."""
    __tablename__ = "user_skills"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)      # username / email, like everywhere else
    skill: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[int] = mapped_column(Integer, default=3)
    category: Mapped[str] = mapped_column(String(30), default="technology")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TeamMemberUser(Base):
    """Links a périmètre's staffed TeamMember to the platform user it represents (no ALTER on team_members)."""
    __tablename__ = "team_member_users"
    member_id: Mapped[str] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(200), index=True)


class PerimeterDeliverable(Base):
    """A generated deliverable of a périmètre (matrice de couverture, plan de charge, COPIL…): the
    structured draft it was built from + the produced file, so later steps reuse it (the plan de
    charge starts from the latest matrice) and agents can read it."""
    __tablename__ = "perimeter_deliverables"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    perimeter_id: Mapped[str] = mapped_column(ForeignKey("perimeters.id"), index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)         # copil | matrice_couverture | plan_de_charge
    title: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    file_id: Mapped[str | None] = mapped_column(String(40), nullable=True)   # AutomationFile holding the bytes
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------- delivery projects (a manager runs one or more; périmètres sit under a project)
# Company → Client → DeliveryProject (a manager) → Perimeter. The project team (ProjectMember) can
# work on any périmètre under the project; when the project is done its members are freed for reassignment.

class DeliveryProject(Base):
    """A client engagement a delivery manager runs. Groups one or more périmètres and a team."""
    __tablename__ = "delivery_projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    manager: Mapped[str] = mapped_column(String(200), default="", index=True)   # username of the delivery manager
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|done|archived
    technologies: Mapped[list] = mapped_column(JSON, default=list)               # chosen tech stack (drives staffing)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    client: Mapped[Client] = relationship()
    perimeters: Mapped[list["Perimeter"]] = relationship(back_populates="project")


class ProjectMember(Base):
    """A user on a project's team. Members work across the project's périmètres; freed when the
    project is done, so they can be staffed on other projects. Members are users (owner = username)."""
    __tablename__ = "project_members"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("delivery_projects.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(String(200), index=True)          # username / email of the user
    role_in_project: Mapped[str] = mapped_column(String(60), default="member")  # manager|lead|member
    allocation_pct: Mapped[int] = mapped_column(Integer, default=100)    # % of capacity committed to this project
    added_by: Mapped[str] = mapped_column(String(200), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProjectDeliverable(Base):
    """A deliverable produced at the PROJECT level (e.g. the plan de charge, which spans all the
    project's périmètres): the structured draft + the produced file. Distinct from PerimeterDeliverable
    (matrice/COPIL live per périmètre); the plan de charge is one document for the whole project."""
    __tablename__ = "project_deliverables"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("delivery_projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)          # plan_de_charge (extensible)
    title: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    file_id: Mapped[str | None] = mapped_column(String(40), nullable=True)   # AutomationFile holding the bytes
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------- livrables: company catalogue + per project
# COPIL / matrice / plan de charge are built-ins. Beyond them, livrables BELONG TO PROJECTS: each project
# picks which catalogue livrables it uses (ProjectLivrable) and can define its own project-only ones
# (CustomDeliverable.project_id set). A livrable with project_id NULL is in the company catalogue
# (published by someone with `deliverables.catalog`) and can be used by any project.

class CustomDeliverable(Base):
    __tablename__ = "custom_deliverables"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)           # who designed it (audit)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("delivery_projects.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")            # the user's own words
    output: Mapped[str] = mapped_column(String(10), default="docx")       # docx | pptx | xlsx | md
    inputs: Mapped[list] = mapped_column(JSON, default=list)              # [{key,label,kind: file|files|text, required, hint}]
    sections: Mapped[list] = mapped_column(JSON, default=list)            # [{title, guidance}]
    instructions: Mapped[str] = mapped_column(Text, default="")           # tone, length, rules
    examples: Mapped[list] = mapped_column(JSON, default=list)            # [{name, text}] — the style it learns from
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProjectLivrable(Base):
    """A catalogue livrable a project uses (key = built-in key, e.g. "copil", or "custom:<id>"). A project
    with no rows yet uses every built-in by default."""
    __tablename__ = "project_livrables"
    __table_args__ = (UniqueConstraint("project_id", "key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("delivery_projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    added_by: Mapped[str] = mapped_column(String(200), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------- people without an account
# Not every company gives the app to every employee: a manager can add a team member manually.
# Their owner key everywhere (ProjectMember.owner, UserSkill.owner, TeamMemberUser.owner) is "ext:<id>".

class ExternalPerson(Base):
    __tablename__ = "external_people"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), default="")
    profile: Mapped[str] = mapped_column(String(60), default="confirmed")   # rate-card key
    weekly_capacity_h: Mapped[float] = mapped_column(Float, default=35.0)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------- AI governance (enterprise controls)

class GovernancePolicy(Base):
    """Org-wide AI governance (single row id="org"): global kill switch, default daily token quota per
    user, and daily token caps per agent."""
    __tablename__ = "governance_policy"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="org")
    ai_enabled: Mapped[bool] = mapped_column(_Boolean, default=True)
    default_daily_tokens: Mapped[int] = mapped_column(Integer, default=0)   # 0 = unlimited
    agent_caps: Mapped[dict] = mapped_column(JSON, default=dict)           # {agent: daily tokens}; 0/absent = unlimited
    updated_by: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class LivrableScope(Base):
    """At which level a project produces one of its livrables (key = built-in key or "custom:<id>"):
    "project" = one document for the whole project, built from all its périmètres (e.g. the plan de charge);
    "perimeter" = generated per périmètre, for the périmètres listed (None = all of them)."""
    __tablename__ = "livrable_scopes"
    __table_args__ = (UniqueConstraint("project_id", "key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("delivery_projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(String(12), default="project")
    perimeter_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UserGovernance(Base):
    """Per-user controls. Keyed by username/email (works in every auth mode, incl. dev personas)."""
    __tablename__ = "user_governance"
    username: Mapped[str] = mapped_column(String(200), primary_key=True)
    app_blocked: Mapped[bool] = mapped_column(_Boolean, default=False)     # no access to the platform at all
    ai_blocked: Mapped[bool] = mapped_column(_Boolean, default=False)      # can use the app, but no AI
    daily_token_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = org default; 0 = unlimited
    note: Mapped[str] = mapped_column(String(300), default="")
    updated_by: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UsageLedger(Base):
    """One row per LLM call, attributed to the user and the agent — the basis for quotas and the
    per-user / per-agent consumption views (LLMCall has no user column; this table is new, no ALTER)."""
    __tablename__ = "usage_ledger"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    username: Mapped[str] = mapped_column(String(200), default="", index=True)
    agent: Mapped[str] = mapped_column(String(60), default="", index=True)
    purpose: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")          # ok | error | fallback | blocked


class UiTranslation(Base):
    """Machine translation of one interface string (French source) into one language, shared by the
    whole organisation so each string is translated once. Numbers in the source are placeholders
    ({0}, {1}…), so "12 tokens" and "40 tokens" share one row."""
    __tablename__ = "ui_translations"
    __table_args__ = (UniqueConstraint("lang", "source_hash"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    lang: Mapped[str] = mapped_column(String(20), index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PolicyDocument(Base):
    """A company policy file an admin uploaded (charter, security rules…) to extract rules from."""
    __tablename__ = "policy_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    filename: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(String(200), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PolicyRule(Base):
    """A company rule every agent and every user must respect (app/policy.py).
    action: block = the request is refused; warn = allowed, the answer opens with a warning;
    guide = only steers the agents. Rules extracted from a document start as `proposed` until reviewed."""
    __tablename__ = "policy_rules"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30), default="autre")   # confidentialite|securite|logiciels|conformite|autre
    action: Mapped[str] = mapped_column(String(10), default="warn")      # block|warn|guide
    keywords: Mapped[list] = mapped_column(JSON, default=list)           # words, or /regex/, that trigger the rule instantly
    active: Mapped[bool] = mapped_column(default=True)
    proposed: Mapped[bool] = mapped_column(default=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("policy_documents.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    updated_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TechWatchProfile(Base):
    """A person's tech-watch preferences on top of the topics derived from their skills and projects."""
    __tablename__ = "tech_watch_profiles"
    owner: Mapped[str] = mapped_column(String(200), primary_key=True)
    extra_topics: Mapped[list] = mapped_column(JSON, default=list)
    muted_topics: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TechWatchBriefing(Base):
    """One generated tech-watch briefing: news matched to the person's topics, with the impact on their work."""
    __tablename__ = "tech_watch_briefings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    items: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    llm_used: Mapped[bool] = mapped_column(default=False)


class LLMProviderConfig(Base):
    """LLM endpoint settings an admin edits in the UI (Modèles & clés API), overriding the .env.
    key = "default" (every agent) or an agent key from app.governance.AGENTS. Empty fields inherit.
    The API key is stored encrypted (app.secret_box) and only ever shown back as `key_hint`."""
    __tablename__ = "llm_provider_configs"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    key_hint: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    updated_by: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

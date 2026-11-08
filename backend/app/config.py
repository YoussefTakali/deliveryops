from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore")

    app_name: str = "DeliveryOps"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'deliveryops.db').as_posix()}"
    log_level: str = "INFO"
    log_json: bool = False
    # "api" serves HTTP, "worker" runs the scheduler (connector sync + proactive watch), "all" does both (dev).
    # In production run the API and exactly one worker.
    role: Literal["all", "api", "worker"] = "all"

    # ---- LLM: any OpenAI-compatible API (NVIDIA API catalog / NIM by default) ----
    # auto: use the API when LLM_API_KEY is set, otherwise deterministic fallbacks. offline (alias mock): never call.
    llm_mode: Literal["auto", "api", "offline", "mock"] = "auto"
    llm_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_api_key: str | None = None
    llm_model_reasoning: str = "meta/llama-3.3-70b-instruct"  # exec summaries, COPIL, onboarding plan
    llm_model_agent: str = "meta/llama-3.3-70b-instruct"  # extraction, analysis, copilot
    llm_model_fast: str = "meta/llama-3.1-8b-instruct"  # routing, live meeting answers
    # Screenshot understanding (automation recordings). OpenAI-style image_url data: URIs.
    llm_model_vision: str = "meta/llama-3.2-11b-vision-instruct"
    # Model that writes the personal-assistant search answer (/api/assistant/ask). Empty = agent tier.
    # A larger model hallucinates less over long mail/file context, at the cost of latency.
    llm_model_search: str | None = None
    # Automation compile with the model's hidden reasoning pass: ~6x slower (measured 84 s vs 13 s)
    # for no visible quality gain on our workflows, since validation + one repair round catch mistakes.
    automation_compile_thinking: bool = False
    # How JSON is obtained: nvext (NIM guided_json), json_schema / json_object (OpenAI response_format), prompt only
    llm_json_mode: Literal["nvext", "json_schema", "json_object", "prompt"] = "nvext"
    # Copilot tool use: native OpenAI "tools", or a JSON action protocol for models without tool calling
    llm_tool_mode: Literal["native", "json"] = "native"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096
    llm_timeout_s: float = 120
    llm_max_retries: int = 4
    llm_max_concurrency: int = 4  # free tiers are rate limited
    # Optional cost accounting, USD per million tokens (leave 0 for free company endpoints)
    llm_price_input_per_m: float = 0.0
    llm_price_output_per_m: float = 0.0
    # Encrypts the API keys an admin enters in the UI (Modèles & clés API, app/llm_config.py) before
    # they are stored. Falls back to AUTH_SECRET. Changing it makes stored keys unreadable (they must
    # be re-entered), so set it once per deployment.
    secrets_key: str | None = None

    # Embeddings — how the RAG dense vector is produced (app/knowledge.py):
    #   hash      : offline feature-hashing, zero setup, weak semantics (lexical score carries retrieval)
    #   fastembed : local multilingual ONNX model (pip install fastembed) — good semantics, offline
    #   api       : real embeddings via the OpenAI-compatible endpoint below (best; needs LLM_API_KEY)
    # After changing this, re-embed existing docs: python -m app.reindex
    embedding_provider: str = "hash"
    embedding_dim: int = 384  # hash vector length / pgvector column width (must match a reindex)
    # `api` provider: model served by LLM_BASE_URL. Verified on the NVIDIA API catalog:
    # nvidia/nemotron-3-embed-1b (dim 2048), nvidia/nv-embedqa-mistral-7b-v2 (dim 4096).
    # Set embedding_dim to the model's dimension, then run `python -m app.reindex`.
    embedding_api_model: str = "nvidia/nemotron-3-embed-1b"
    embedding_api_batch: int = 32
    # Passage embedding (ingestion / reindex, in the background): wait longer so vectors are always the
    # real thing. Query embedding (interactive search): a tight cap so a slow/cold endpoint falls back
    # to the lexical score fast instead of hanging the request; repeated queries are cached anyway.
    embedding_api_timeout_s: float = 60
    embedding_api_query_timeout_s: float = 12
    # Rerank the top retrieval candidates with an LLM pass (real semantic relevance, not keywords),
    # on the interactive search/ask endpoints only. Off by default: it adds a round-trip, and with a
    # large model on the `fast` tier that is slow. Turn on only when LLM_MODEL_FAST is a small, quick
    # model (e.g. meta/llama-3.1-8b-instruct) — the `api` embeddings already give real semantic search.
    knowledge_rerank: bool = False
    # Retrieval score above which the live call copilot treats a turn as "about the selected project"
    # and grounds its answer in the project's docs. The copilot retrieves lexically (dense=False) for
    # speed, so this is tuned to that score scale; it's one of three signals (the others: the project
    # name being mentioned, and project vocabulary), so a moderate bar that catches real project turns
    # without latching onto an off-topic keyword works best.
    knowledge_strong_hit: float = 0.15

    # ---- Speech-to-text (call-assistant "listen to the whole call" mode, app/stt.py) ----
    # Local faster-whisper (CTranslate2) — no cloud API key, runs on CPU. "tiny"/"base"/"small" trade
    # accuracy for latency; "small" is a reasonable default for short (4-6s) chunks on a laptop CPU.
    stt_model: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"  # int8 quantization: much faster on CPU, small accuracy cost

    # Speaker diarization for the call copilot ("who is speaking"). A per-utterance voice embedding is
    # returned so the client can cluster participants.
    #   onnx  : a trained speaker model (WeSpeaker ResNet34) via onnxruntime — accurate, ~40ms/utterance
    #   fbank : a dependency-free numpy log-mel fingerprint (fallback if the ONNX model can't load)
    #   off   : no diarization
    speaker_embedding: Literal["onnx", "fbank", "off"] = "onnx"
    speaker_onnx_repo: str = "onnx-community/wespeaker-voxceleb-resnet34-LM"
    speaker_onnx_file: str = "onnx/model_quantized.onnx"

    # ---- Auth ----
    # microsoft = Entra ID (Azure AD) SSO: the browser silently picks up the Microsoft account
    # signed into the PC (MSAL ssoSilent), and the API authenticates each request by validating the
    # Microsoft Graph access token the SPA sends (see app/auth.py -> _microsoft_user).
    # native = built-in email/password login + editable role→permission RBAC (default, no external
    # IdP). keycloak/microsoft = SSO. dev = persona switcher (local only, forbidden in production).
    auth_mode: Literal["native", "dev", "keycloak", "microsoft"] = "native"
    # Native auth (HS256 token signing + refresh cookies). Change AUTH_SECRET in every real deploy.
    auth_secret: str = "dev-insecure-change-me"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    auth_cookie_secure: bool | None = None  # None -> secure cookies in production only
    # First-boot admin. If no users exist, one is seeded from these (a random password is
    # generated and logged once if the password is left empty).
    initial_admin_email: str = "admin@deliveryops.local"
    initial_admin_password: str | None = None
    initial_admin_name: str = "Administrateur"
    # Native accounts: lock after N consecutive failed sign-ins, for M minutes.
    auth_lockout_attempts: int = 5
    auth_lockout_minutes: int = 15
    password_reset_ttl_minutes: int = 30
    # SMTP for "mot de passe oublié" e-mails (native auth). Unset: the reset link is only written to the
    # server log, and only outside production — an admin reset (temporary password) still works.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # e.g. "DeliveryOps <no-reply@example.com>"
    smtp_starttls: bool = True
    # Sign-in methods for AUTH_MODE=native, comma-separated: password, microsoft, google. Every method
    # opens the same DeliveryOps session; a person can link several to one account (Mon compte).
    # microsoft/google use OpenID Connect (authorization code + PKCE) with the redirect URI
    # {FRONTEND_URL}/api/auth/oidc/<provider>/callback registered on the provider side.
    auth_providers: str = "password"
    # Who may create an account by signing in with Microsoft/Google (comma-separated e-mail domains,
    # e.g. "acme.com,acme.fr"). Empty = anyone, but they get no access until an admin assigns a role.
    auth_allowed_domains: str = ""
    microsoft_client_secret: str | None = None  # server-side sign-in (the SPA/MSAL data connector needs none)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_hosted_domain: str | None = None  # restrict Google sign-in to one Workspace domain
    keycloak_issuer: str = "http://localhost:8280/realms/deliveryops"  # "iss" as written in tokens (public URL)
    keycloak_jwks_url: str | None = None  # internal URL the API uses to fetch signing keys
    keycloak_audience: str = "deliveryops-api"
    keycloak_client_id: str = "deliveryops-app"
    keycloak_public_url: str | None = None  # base URL browsers use, defaults to the issuer
    # Service account used by the in-app user administration (client credentials grant)
    keycloak_admin_base_url: str | None = None  # internal base, e.g. http://keycloak:8080/auth
    keycloak_admin_client_id: str = "deliveryops-admin"
    keycloak_admin_client_secret: str | None = None

    # ---- Microsoft Entra ID / Graph (auth_mode=microsoft, or as a data connector) ----
    microsoft_tenant_id: str | None = None  # your Entra tenant id (GUID) or "common"/"organizations"
    microsoft_client_id: str | None = None  # the app registration's Application (client) ID
    # Scopes are split into three tiers because Entra evaluates consent per *request*, not per
    # scope: if several scopes are requested together and even one needs tenant-admin approval that
    # the org's policy doesn't allow a user to self-grant, Entra refuses the WHOLE request — including
    # the harmless ones — with an "Approval required" screen. Bundling everything into sign-in itself
    # (the original bug here) meant a restrictive tenant (e.g. a university) couldn't sign in at all.
    #   - login: the only scope requested during actual sign-in. Always self-consentable.
    #   - basic: read-only "own data" Graph scopes essentially every tenant lets a user self-consent.
    #   - admin: Graph scopes Microsoft classifies as high-privilege; most tenants require a tenant
    #     admin to grant these org-wide (see the "Accorder le consentement admin" flow on the
    #     Microsoft page). Requested separately so a tenant blocking these doesn't also block login
    #     or the basic sources.
    microsoft_login_scopes: str = "User.Read"
    microsoft_graph_scopes_basic: str = "User.Read Mail.Read Calendars.Read Tasks.Read Presence.Read"
    microsoft_graph_scopes_admin: str = "Files.Read.All Sites.Read.All Chat.Read Team.ReadBasic.All"
    # Automations that send mail (mail.send step) need delegated Mail.Send. Requested on its own,
    # only by the automation runner (incremental consent), never at sign-in.
    microsoft_graph_scopes_automation: str = "Mail.Send"
    # Full set, used opportunistically when a tenant has already granted admin consent for everything.
    microsoft_graph_scopes: str = f"{microsoft_graph_scopes_basic} {microsoft_graph_scopes_admin}"

    # ---- Platform ----
    teams_webhook_url: str | None = None
    # Directory the desktop installer(s) are served from (GET /api/download/desktop). Drop the
    # electron-builder output here (e.g. "DeliveryOps Setup x.y.z.exe").
    downloads_dir: str = str(BACKEND_DIR / "downloads")
    frontend_url: str = "http://localhost:4300"
    cors_origins: str = "http://localhost:4300"
    watch_enabled: bool = True
    watch_interval_minutes: int = 15
    # Proactive AI suggestions (risk/billing agents run in read-only mode, on a cadence slower than
    # the deterministic checks above) — see app/watch.py:ai_scan_all(). Runs every Nth watch cycle,
    # i.e. every watch_interval_minutes * watch_ai_every_n_cycles minutes, and only calls the LLM
    # again for a perimeter once its underlying data has actually changed.
    watch_ai_enabled: bool = True
    watch_ai_every_n_cycles: int = 4
    watch_ai_agents: str = "risk"  # comma-separated subset of: risk, billing
    seed_demo_data: bool = True
    max_upload_mb: int = 25
    rate_limit_per_minute: int = 240  # per user, all API calls
    rate_limit_llm_per_minute: int = 20  # per user, calls that reach the LLM
    # Bulk ingest (personal workspace scan: local files / Microsoft) is cheap per call (chunk +
    # embed, no LLM) but legitimately high-frequency — a full-disk index or the desktop app's live
    # file watcher can fire far more than the general budget in a burst. Its own generous bucket
    # keeps it from either starving normal browsing or getting throttled by it.
    rate_limit_bulk_per_minute: int = 1200

    @property
    def use_llm(self) -> bool:
        if self.llm_mode in ("offline", "mock"):
            return False
        if self.llm_mode == "api":
            return True
        if self.llm_api_key:
            return True
        from app import llm_config  # late import: keys can also be entered in the UI
        return llm_config.any_key()

    @property
    def cookie_secure(self) -> bool:
        return self.is_production if self.auth_cookie_secure is None else self.auth_cookie_secure

    @property
    def microsoft_enabled(self) -> bool:
        return bool(self.microsoft_tenant_id and self.microsoft_client_id)

    @property
    def sign_in_methods(self) -> list[str]:
        """Native-mode sign-in methods that are both requested and fully configured."""
        wanted = [p.strip().lower() for p in self.auth_providers.split(",") if p.strip()]
        ready = {
            "password": True,
            "microsoft": bool(self.microsoft_enabled and self.microsoft_client_secret),
            "google": bool(self.google_client_id and self.google_client_secret),
        }
        return [p for p in dict.fromkeys(wanted) if ready.get(p)]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def runs_api(self) -> bool:
        return self.role in ("all", "api")

    @property
    def runs_worker(self) -> bool:
        return self.role in ("all", "worker")

    @model_validator(mode="after")
    def _production_guards(self):
        if self.environment != "production":
            return self
        problems = []
        if self.auth_mode not in ("native", "keycloak", "microsoft"):
            problems.append("AUTH_MODE must be native, keycloak or microsoft")
        if self.auth_mode == "native" and self.auth_secret in ("", "dev-insecure-change-me"):
            problems.append("AUTH_SECRET must be set to a strong random value when AUTH_MODE=native")
        if self.auth_mode == "microsoft" and not self.microsoft_enabled:
            problems.append("MICROSOFT_TENANT_ID and MICROSOFT_CLIENT_ID are required when AUTH_MODE=microsoft")
        if self.auth_mode == "native":
            wanted = {p.strip().lower() for p in self.auth_providers.split(",") if p.strip()}
            unknown = wanted - {"password", "microsoft", "google"}
            if unknown:
                problems.append(f"AUTH_PROVIDERS: unknown method(s) {', '.join(sorted(unknown))}")
            if missing := sorted(wanted - set(self.sign_in_methods) - unknown):
                problems.append(f"AUTH_PROVIDERS lists {', '.join(missing)} but its client id/secret settings are missing")
            if not self.sign_in_methods:
                problems.append("AUTH_PROVIDERS must enable at least one sign-in method")
        if not self.is_postgres:
            problems.append("DATABASE_URL must point to PostgreSQL")
        if self.seed_demo_data:
            problems.append("SEED_DEMO_DATA must be false")
        if "*" in self.cors_origins or "localhost" in self.cors_origins:
            problems.append("CORS_ORIGINS must list the public HTTPS origin only")
        if self.llm_mode == "api" and not self.llm_api_key:
            problems.append("LLM_API_KEY is required when LLM_MODE=api")
        if self.role == "all":
            problems.append("ROLE must be api or worker (run one worker container)")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

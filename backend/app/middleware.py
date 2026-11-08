"""Production middleware: request correlation, structured logging, security headers,
per-user rate limiting, and a body-size cap. Registered by app.main in path order:
request id -> access log -> security headers -> rate limit -> body size -> routes.

Rate limiting is in-memory (per-process). That's correct for a single API replica behind a
reverse proxy, which is what docker-compose.prod.yml runs; scaling to multiple replicas needs a
shared store (Redis) instead — swap `_Bucket` for a Redis-backed one without changing the
middleware's interface.
"""

import json
import logging
import re
import time
import uuid
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings

settings = get_settings()
log = logging.getLogger("deliveryops.access")

# Paths that invoke the LLM get the tighter budget (see Settings.rate_limit_llm_per_minute).
_LLM_PATH = re.compile(r"/(copilot|runs|ask|contract/extract|meetings/import|live/respond)(/|$|\?)")
# Bulk ingest (personal workspace scan) gets its own generous budget instead of the general one —
# see Settings.rate_limit_bulk_per_minute.
_BULK_PATH = re.compile(r"/assistant/scan/(local|microsoft)(/|$|\?)")
# Automations: the runner legitimately fires many cheap calls per run (one per step and per loop
# iteration, plus progress logs and file transfers) -> bulk bucket. The endpoints that always call
# the LLM go to the LLM bucket; server steps that only *sometimes* call the LLM (ai.generate, table
# extraction from a PDF) charge the LLM bucket from inside the route via `consume_llm_quota`.
_AUTOMATION_PATH = re.compile(r"^/api/automations(/|$)")
_AUTOMATION_LLM_PATH = re.compile(r"^/api/automations/(compile|recordings/[^/]+/analyze)/?$")
_AUTOMATION_BULK_PATH = re.compile(r"^/api/automations/(runs/[^/]+/(steps|log)|files|recordings)(/|$)")


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line — easy to ship to a log aggregator (Loki, CloudWatch, ELK)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for attr in ("request_id", "user", "method", "path", "status", "duration_ms"):
            if hasattr(record, attr):
                payload[attr] = getattr(record, attr)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter() if settings.log_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]
    # uvicorn's own access log is redundant with ours and noisier; keep error logs only.
    logging.getLogger("uvicorn.access").disabled = True


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id (reusing an inbound X-Request-Id from the reverse proxy when
    present), logs one line per request, and never lets a request fail to log on exception."""

    async def dispatch(self, request: Request, call_next):
        from app import telemetry  # late import: telemetry pulls in the models
        telemetry.start_request()  # per-request user holder → LLM calls are attributed to the user
        telemetry.bind_language(request.headers.get("x-ui-language", ""), request.headers.get("x-ui-language-name", ""))
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "unhandled exception", extra={"request_id": request_id, "user": getattr(request.state, "user", "-"), "method": request.method, "path": request.url.path}
            )
            response = JSONResponse({"detail": "Internal server error"} if settings.is_production else {"detail": "Internal server error", "request_id": request_id}, status_code=500)
        duration_ms = int((time.monotonic() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        log.info(
            "request", extra={
                "request_id": request_id, "user": getattr(request.state, "user", "-"), "method": request.method,
                "path": request.url.path, "status": response.status_code, "duration_ms": duration_ms,
            },
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=(), payment=()"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


class _Bucket:
    """Fixed-window counter per key. Good enough for abuse protection; not exact leaky-bucket."""

    __slots__ = ("count", "window_start")

    def __init__(self):
        self.count = 0
        self.window_start = 0.0


def _hit_bucket(buckets: dict[str, _Bucket], key: str, limit: int) -> tuple[bool, int]:
    now = time.time()
    bucket = buckets[key]
    if now - bucket.window_start >= 60:
        bucket.window_start, bucket.count = now, 0
    bucket.count += 1
    retry_after = max(1, int(60 - (now - bucket.window_start)))
    return bucket.count <= limit, retry_after


def rate_key(request: Request) -> str:
    return request.headers.get("x-dev-user") or (request.headers.get("authorization") or (request.client.host if request.client else "") or "anon")[-64:]


# Shared (module-level) so routes can charge the LLM budget for calls the path alone can't classify.
_LLM_BUCKETS: dict[str, _Bucket] = defaultdict(_Bucket)


def consume_llm_quota(request: Request) -> None:
    """Charge one LLM call to the caller's LLM bucket; raises HTTP 429 when exhausted."""
    from fastapi import HTTPException

    from app import governance  # AI governance (blocked user / daily quota / agent caps) — clear 403/429
    governance.raise_if_ai_denied(getattr(request.state, "user", "") or "")
    ok, retry_after = _hit_bucket(_LLM_BUCKETS, rate_key(request), settings.rate_limit_llm_per_minute)
    if not ok:
        raise HTTPException(429, "Too many AI requests, please slow down", headers={"Retry-After": str(retry_after)})


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._general: dict[str, _Bucket] = defaultdict(_Bucket)
        self._llm: dict[str, _Bucket] = _LLM_BUCKETS
        self._bulk: dict[str, _Bucket] = defaultdict(_Bucket)

    def _hit(self, buckets: dict[str, _Bucket], key: str, limit: int) -> tuple[bool, int]:
        return _hit_bucket(buckets, key, limit)

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
            return await call_next(request)
        key = rate_key(request)
        path = request.url.path

        if _AUTOMATION_PATH.match(path):
            if request.method == "POST" and _AUTOMATION_BULK_PATH.match(path):
                buckets, limit = self._bulk, settings.rate_limit_bulk_per_minute
            else:
                buckets, limit = self._general, settings.rate_limit_per_minute
            ok, retry_after = self._hit(buckets, key, limit)
            if ok and request.method == "POST" and _AUTOMATION_LLM_PATH.match(path):
                ok, retry_after = self._hit(self._llm, key, settings.rate_limit_llm_per_minute)
            if not ok:
                return JSONResponse({"detail": "Trop de requêtes, veuillez patienter"}, status_code=429, headers={"Retry-After": str(retry_after)})
            return await call_next(request)

        if _BULK_PATH.search(request.url.path):
            ok, retry_after = self._hit(self._bulk, key, settings.rate_limit_bulk_per_minute)
            if not ok:
                return JSONResponse({"detail": "Indexation trop rapide, ralentissement en cours"}, status_code=429, headers={"Retry-After": str(retry_after)})
            return await call_next(request)

        ok, retry_after = self._hit(self._general, key, settings.rate_limit_per_minute)
        if not ok:
            return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": str(retry_after)})

        if _LLM_PATH.search(request.url.path) and request.method == "POST":
            ok, retry_after = self._hit(self._llm, key, settings.rate_limit_llm_per_minute)
            if not ok:
                return JSONResponse({"detail": "Too many AI requests, please slow down"}, status_code=429, headers={"Retry-After": str(retry_after)})

        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Best-effort cap via Content-Length. The reverse proxy (nginx `client_max_body_size`)
    is the primary defense — this backstops direct access to the API port."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.max_upload_mb * 1024 * 1024:
            return JSONResponse({"detail": f"Request body exceeds {settings.max_upload_mb} MB"}, status_code=413)
        return await call_next(request)

#!/bin/sh
# Entrypoint for all DeliveryOps backend roles (api / worker / mcp). The role is the first
# argument, set per-service by docker-compose.yml / docker-compose.prod.yml.
set -e

python - <<'PY'
# Best-effort wait for the database to accept connections. docker-compose's
# `depends_on: condition: service_healthy` already gates this in normal operation; this is a
# backstop for orchestrators that don't honor that (or a Postgres restart mid-deploy).
import sys
import time

from app.db import engine

for attempt in range(30):
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"waiting for database... ({attempt + 1}/30): {exc}", file=sys.stderr)
        time.sleep(2)
else:
    print("database not reachable after 60s, giving up", file=sys.stderr)
    sys.exit(1)
PY

role="${1:-api}"
shift || true

case "$role" in
  api)
    if [ "$ENVIRONMENT" = "production" ]; then
      echo "Applying database migrations..."
      alembic upgrade head
    fi
    if [ "$SEED_DEMO_DATA" = "true" ]; then
      echo "Seeding demo data (idempotent, no-op if the database already has data)..."
      python -m app.seed.seed
    fi
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'
    ;;
  worker)
    exec python -m app.worker
    ;;
  mcp)
    exec python -m app.mcp_server --http 8765
    ;;
  *)
    # anything else (e.g. a shell for debugging, alembic invoked directly) runs as-is
    exec "$role" "$@"
    ;;
esac

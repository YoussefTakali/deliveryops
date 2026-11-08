"""Standalone worker process: connector sync + proactive watch, without an HTTP server.

Run as the container's foreground process for ROLE=worker in production (docker-compose.prod.yml)
so the scheduler survives API redeploys/restarts and only ever runs in one place. For local dev
(ROLE=all, the default) the same loop runs in a background thread instead — see
app.watch.start_scheduler(), started from app.main's lifespan.

    python -m app.worker
"""

import logging
import signal
import threading

from app.config import get_settings
from app.db import init_db
from app.middleware import configure_logging
from app.watch import run_forever

log = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging()
    init_db()

    if not settings.watch_enabled:
        log.warning("WATCH_ENABLED is false; worker has nothing to do and will idle")

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("worker started (interval=%sm)", settings.watch_interval_minutes)
    run_forever(stop)
    log.info("worker stopped")


if __name__ == "__main__":
    main()

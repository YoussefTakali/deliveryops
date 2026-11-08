# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Build stage: compile wheels for anything without a manylinux binary (psycopg, cryptography...)
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# Runtime stage: no compilers, no build deps, non-root user
FROM base AS runtime
RUN groupadd --gid 1000 deliveryops && useradd --uid 1000 --gid deliveryops --create-home --shell /usr/sbin/nologin deliveryops
COPY --from=builder /root/.local /home/deliveryops/.local
ENV PATH=/home/deliveryops/.local/bin:$PATH

COPY --chown=deliveryops:deliveryops app ./app
COPY --chown=deliveryops:deliveryops alembic ./alembic
COPY --chown=deliveryops:deliveryops alembic.ini .
COPY --chown=deliveryops:deliveryops docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh
# Pre-create the Whisper model cache dir owned by the app user: a named volume mounted here is
# initialised from this directory's ownership, otherwise it comes up root-owned and the model can
# never be downloaded (every transcription then fails and the call assistant hears nothing).
RUN mkdir -p /home/deliveryops/.cache/huggingface && chown -R deliveryops:deliveryops /home/deliveryops/.cache

USER deliveryops
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["api"]

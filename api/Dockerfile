# Base image pinned by digest (python:3.12-slim, bookworm) so a rebuild cannot
# silently pick up a different image; the digest is refreshed by a reviewed PR.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime dependencies only (requirements-dev.txt holds the test tooling).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY scripts ./scripts

# Unprivileged runtime user. The code tree is read only for it; the local
# SQLite fallback below needs one writable directory.
RUN groupadd --system --gid 10001 hca \
    && useradd --system --uid 10001 --gid hca --home-dir /app --no-create-home hca \
    && mkdir -p /data && chown hca:hca /data \
    && chown -R root:root /app && chmod -R a-w /app
USER hca:hca

# Local fallback only; Cloud Run injects DATABASE_URL from Secret Manager.
ENV DATABASE_URL=sqlite:////data/hca.db
ENV PORT=8080
EXPOSE 8080

# One uvicorn worker per container: Cloud Run scales by instance, and the
# event fan out between instances is Postgres LISTEN/NOTIFY (app/events.py,
# P20), so several instances see each other's events. The migrate job, not
# the container start, runs alembic.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

# Minimal musl runtime pinned by digest (Python 3.12, Alpine 3.24) so a rebuild cannot
# silently pick up a different image; the digest is refreshed by a reviewed PR.
FROM python:3.12-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime dependencies only. Require hash-approved musllinux/pure wheels;
# refuse an unexpected source build or added compiler/toolchain dependency.
COPY requirements.lock .
# Explicit Alpine patch; keep the version and official source in the pin record.
RUN apk add --upgrade --no-cache zlib=1.3.2-r1 \
    && pip install --require-hashes --only-binary=:all: -r requirements.lock \
    && python -m pip uninstall --yes pip setuptools wheel \
    && python -c 'import shutil, sysconfig; from pathlib import Path; shutil.rmtree(Path(sysconfig.get_path("stdlib")) / "ensurepip")'
# Installation tooling and its bundled installer are absent from the runtime.

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY scripts ./scripts

# Unprivileged runtime user. The code tree is read only for it; the local
# SQLite fallback below needs one writable directory.
RUN addgroup -S -g 10001 hca \
    && adduser -S -D -H -u 10001 -G hca -h /app hca \
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

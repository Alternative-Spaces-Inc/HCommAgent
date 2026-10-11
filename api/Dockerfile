FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY scripts ./scripts

# Local fallback only; Cloud Run injects DATABASE_URL from Secret Manager.
ENV DATABASE_URL=sqlite:////app/hca.db
ENV PORT=8080
EXPOSE 8080

# One worker on purpose: app/events.py is an in-process pub/sub, so a second
# worker (or a second instance) would not see events published by the first.
# The runbook pins max-instances to 1 for the same reason until a shared bus
# (Postgres LISTEN/NOTIFY or Redis) lands; the extension's alarm fallback
# covers any missed push meanwhile.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

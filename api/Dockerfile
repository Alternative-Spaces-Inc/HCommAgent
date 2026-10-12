FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 hca \
    && useradd --uid 10001 --gid 10001 --no-create-home hca \
    && mkdir /data && chown hca:hca /data
COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY scripts ./scripts
ENV DATABASE_URL=sqlite:////data/hca.db
USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

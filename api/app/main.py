from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .db import db_ping
from .requests_api import router as requests_router, audit_router
from .inbox_api import router as inbox_router
from .apikeys_api import router as apikeys_router
from .sweep_api import router as sweep_router
from .metrics_api import router as metrics_router
from .devices_api import router as devices_router
from .privacy import router as privacy_router
from .webhooks_api import router as webhooks_router
from .tenancy import router as tenants_router
from .data_api import router as data_router
from . import limits  # noqa: F401 -- register persistent counters before metadata creation
from .middleware import RequestBounds


@asynccontextmanager
async def lifespan(app):
    from .config import validate_environment
    from .db import SessionLocal
    from . import models, events, jobs
    validate_environment()
    with SessionLocal() as db:
        models.ensure_default_tenant(db)
    try:
        events.start_listener()
        jobs.start_worker()
        yield
    finally:
        jobs.stop_worker()
        events.stop_listener()


app = FastAPI(title="Human Collections Agent (HCA) API", version="0.3.0", lifespan=lifespan,
    responses={
        413: {"description": "Request body exceeds 1 MiB"},
        429: {"description": "Shared rate limit or tenant quota reached", "headers": {
            "Retry-After": {"description": "Seconds until retry", "schema": {"type": "string"}}}},
    })
app.add_middleware(RequestBounds)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Validation errors must not echo credentials or non-JSON input values.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()]})


app.include_router(requests_router)
app.include_router(audit_router)
app.include_router(inbox_router)
app.include_router(apikeys_router)
app.include_router(sweep_router)
app.include_router(metrics_router)
app.include_router(devices_router)
app.include_router(tenants_router)
app.include_router(data_router)
app.include_router(privacy_router)
app.include_router(webhooks_router)


@app.get("/healthz")
@app.get("/health")
def healthz():
    """200 {"status": "ok", "db": "ok"} when the database answers SELECT 1;
    503 {"status": "degraded", "db": "down"} when it does not. Cloud Run's
    startup probe and the plan's Day 1 acceptance both read this route, so a
    dead database must be a clean 503, not an unhandled 500.

    Two paths on purpose. /healthz is what the container probe hits from
    inside Cloud Run. From the public URL the Google frontend answers exactly
    /healthz itself with an HTML 404 and never forwards it (seen on the first
    deploy, 2026-09-30: /healthz gave Google's 404 page while /healthzz and
    /healthz/ reached FastAPI). /health is the path external checks use."""
    try:
        ok = db_ping()
    except Exception:
        ok = False
    if not ok:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
    # HCA-P20 Day 2: the fan-out listener's state, so a deploy can prove the
    # instance really is on LISTEN (poll fallback keeps delivery either way).
    from . import events, jobs
    ls = events.listener_state
    js = jobs.worker_state
    return {"status": "ok", "db": "ok",
            "events": {"listener": "connected" if ls["connected"] else ("poll" if not ls["running"] else "reconnecting"),
                       "notifications": ls["notifications"], "errors": ls["errors"]},
            "jobs": {"worker": "running" if js["running"] else "stopped", "polls": js["polls"], "claimed": js["claimed"],
                     "done": js["done"], "failed": js["failed"]}}


_default_openapi = app.openapi


def correlated_openapi():
    schema = _default_openapi()
    for path in schema["paths"].values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                response.setdefault("headers", {})["X-Request-ID"] = {
                    "description": "Server-generated correlation identifier; excludes request content",
                    "schema": {"type": "string"},
                }
    return schema


app.openapi = correlated_openapi

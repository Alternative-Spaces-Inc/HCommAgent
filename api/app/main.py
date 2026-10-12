from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .db import db_ping
from .requests_api import router as requests_router, audit_router
from .inbox_api import router as inbox_router
from .apikeys_api import router as apikeys_router
from .sweep_api import router as sweep_router
from .controls_api import router as controls_router
from .metrics_api import router as metrics_router
from .devices_api import router as devices_router
from .privacy import router as privacy_router
from .webhooks_api import router as webhooks_router
from .tenancy import router as tenants_router

app = FastAPI(title="Human Collections Agent (HCA) API")
app.include_router(requests_router)
app.include_router(audit_router)
app.include_router(inbox_router)
app.include_router(apikeys_router)
app.include_router(sweep_router)
app.include_router(controls_router)
app.include_router(metrics_router)
app.include_router(devices_router)
app.include_router(tenants_router)
app.include_router(privacy_router)
app.include_router(webhooks_router)


@app.on_event("startup")
async def _ensure_default_tenant():
    from .config import validate_environment
    validate_environment()
    from .db import SessionLocal
    from . import models
    with SessionLocal() as db:
        models.ensure_default_tenant(db)


@app.on_event("startup")
async def _start_event_listener():
    # HCA-P20 Day 2: on Postgres, LISTEN for rows other instances publish;
    # on SQLite this is a no-op and the 2 s poll plus in-process wake deliver.
    from . import events
    events.start_listener()


@app.on_event("shutdown")
async def _stop_event_listener():
    from . import events
    events.stop_listener()


@app.on_event("startup")
async def _start_jobs_worker():
    # HCA-P20 Day 3: escalation and expiry run as leased scheduled_jobs rows
    # claimed by this thread (app/jobs.py); the P14 loop timers are gone.
    from . import jobs
    jobs.start_worker()


@app.on_event("shutdown")
async def _stop_jobs_worker():
    from . import jobs
    jobs.stop_worker()


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

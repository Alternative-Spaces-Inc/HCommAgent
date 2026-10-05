from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .db import db_ping
from .requests_api import router as requests_router, audit_router
from .inbox_api import router as inbox_router
from .apikeys_api import router as apikeys_router
from .sweep_api import router as sweep_router
from .devices_api import router as devices_router

app = FastAPI(title="Human Collections Agent (HCA) API")
app.include_router(requests_router)
app.include_router(audit_router)
app.include_router(inbox_router)
app.include_router(apikeys_router)
app.include_router(sweep_router)
app.include_router(devices_router)


@app.on_event("startup")
async def _bind_routing_loop():
    # HCA-P14: escalation timers (app/routing.py) run on the server loop.
    import asyncio
    from . import routing
    routing.bind_loop(asyncio.get_running_loop())


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
    return {"status": "ok", "db": "ok"}

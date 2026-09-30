from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .db import db_ping
from .requests_api import router as requests_router, audit_router
from .inbox_api import router as inbox_router
from .apikeys_api import router as apikeys_router
from .sweep_api import router as sweep_router

app = FastAPI(title="Human Collections Agent (HCA) API")
app.include_router(requests_router)
app.include_router(audit_router)
app.include_router(inbox_router)
app.include_router(apikeys_router)
app.include_router(sweep_router)


@app.get("/healthz")
def healthz():
    """200 {"status": "ok", "db": "ok"} when the database answers SELECT 1;
    503 {"status": "degraded", "db": "down"} when it does not. Cloud Run's
    startup probe and the plan's Day 1 acceptance both read this route, so a
    dead database must be a clean 503, not an unhandled 500."""
    try:
        ok = db_ping()
    except Exception:
        ok = False
    if not ok:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
    return {"status": "ok", "db": "ok"}

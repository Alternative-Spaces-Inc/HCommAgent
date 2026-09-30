from fastapi import FastAPI
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
    ok = db_ping()
    return {"status": "ok", "db": "ok" if ok else "down"}

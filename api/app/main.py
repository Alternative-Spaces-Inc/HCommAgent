from fastapi import FastAPI
from .db import db_ping
from .requests_api import router as requests_router

app = FastAPI(title="Human Collections Agent (HCA) API")
app.include_router(requests_router)


@app.get("/healthz")
def healthz():
    ok = db_ping()
    return {"status": "ok", "db": "ok" if ok else "down"}

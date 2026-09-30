from fastapi import FastAPI
from .db import db_ping

app = FastAPI(title="Human Collections Agent (HCA) API")


@app.get("/healthz")
def healthz():
    ok = db_ping()
    return {"status": "ok", "db": "ok" if ok else "down"}

"""The scheduler-hit trigger for the auto-deny expiry sweep (app/expiry.py).

docs/plans/HCA_CLOUD_RUN_PLAN_v1.md names a Cloud Run *service* (not a
separate Cloud Run *job* resource) as the one deployed unit, always on
(min-instances 1) for the SSE engine; the sweep is folded into that same
FastAPI service as a bearer-guarded endpoint for Cloud Scheduler's HTTP
target to hit, rather than inventing a second deployable (a Cloud Run
job) the plan does not name. This is the plan's stated pattern for a
periodic hit against the running service, not a new decision.

SWEEP_TRIGGER_TOKEN is read from the environment (Secret Manager at
deploy time, per the plan's Secret Management section); this sub-task
sets no secret and ships no value; a missing token fails closed (403)
rather than open.
"""

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .expiry import run_expiry_sweep

router = APIRouter(prefix="/v1/internal", tags=["internal"])


def _require_sweep_bearer(authorization: str = Header(default=None)) -> None:
    expected = os.environ.get("SWEEP_TRIGGER_TOKEN")
    if not expected:
        raise HTTPException(status_code=403, detail="sweep trigger token is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    presented = authorization[len("Bearer "):]
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=403, detail="invalid bearer token")


@router.post("/expiry-sweep")
def trigger_expiry_sweep(db: Session = Depends(get_db), _auth=Depends(_require_sweep_bearer)):
    results = run_expiry_sweep(db)
    return {"swept": len(results), "results": results}

"""GET /v1/internal/metrics (HCA-P20 Day 4): the per-instance metrics page
from app/metrics.py, under the platform admin bearer. Cloud Run routes each
call to one instance, so a reader that wants every instance calls until it
has seen each instance id, or reads the shared numbers (events.newest_id,
jobs.due_backlog) from any one of them.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .auth import require_admin_bearer
from .db import get_db
from . import metrics

router = APIRouter(prefix="/v1/internal", tags=["internal"])


@router.get("/metrics")
def read_metrics(db: Session = Depends(get_db), _auth=Depends(require_admin_bearer)):
    return metrics.collect(db)

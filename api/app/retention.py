"""Bounded removal of expired transient delivery state, preserving audit records."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from . import models


def cleanup(db, now=None, batch=500):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=7)
    removed = {}
    for model, time_field in ((models.Event, models.Event.created_at),
                              (models.ScheduledJob, models.ScheduledJob.done_at)):
        ids = list(db.execute(select(model.id).where(time_field < cutoff).limit(batch)).scalars())
        if ids:
            db.execute(delete(model).where(model.id.in_(ids)))
        removed[model.__tablename__] = len(ids)
    db.commit()
    return removed

"""Bounded transient-record retention; decision audits require explicit purge.

Defaults: events and completed jobs 7 days, terminal webhook deliveries and
attempts 30 days, destination registry events 90 days. Active deliveries/jobs
are retained. The event retention always covers the configured replay window.
Cleanup runs on the worker's reconciliation interval and the sweep endpoint.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, exists, func, select, tuple_

from . import devicesig, events, idempotency, models
from .limits import RateLimitBucket

EVENT_RETENTION_DAYS = 7
JOB_RETENTION_DAYS = 7
WEBHOOK_RETENTION_DAYS = 30
REGISTRY_EVENT_RETENTION_DAYS = 90
MAX_CLEANUP_BATCH = 500


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _remove(db, model, predicate, batch):
    ids = list(db.execute(select(model.id).where(predicate).order_by(model.id).limit(batch)).scalars())
    if not ids:
        return 0
    # Repeat the predicate so a concurrently resumed record is not deleted.
    return db.execute(delete(model).where(model.id.in_(ids), predicate)).rowcount or 0


def cleanup(db, now=None, batch=MAX_CLEANUP_BATCH):
    now = _naive(now or datetime.now(timezone.utc))
    batch = max(1, min(batch, MAX_CLEANUP_BATCH))
    event_age = max(timedelta(days=EVENT_RETENTION_DAYS), timedelta(seconds=events.REPLAY_WINDOW_SECONDS))
    webhook_cutoff = now - timedelta(days=WEBHOOK_RETENTION_DAYS)
    active_delivery = exists(select(models.WebhookDelivery.id).where(
        models.WebhookDelivery.id == models.WebhookAttempt.delivery_id,
        models.WebhookDelivery.state == "queued"))
    terminal_time = func.coalesce(models.WebhookDelivery.delivered_at, models.WebhookDelivery.dead_at,
                                  models.WebhookDelivery.created_at)
    open_delivery_job = exists(select(models.ScheduledJob.id).where(
        models.ScheduledJob.ref_id == models.WebhookDelivery.id,
        models.ScheduledJob.done_at.is_(None)))
    removed = {}
    predicates = (
        (models.Event, models.Event.created_at < now - event_age),
        (models.ScheduledJob, models.ScheduledJob.done_at < now - timedelta(days=JOB_RETENTION_DAYS)),
        (models.WebhookAttempt, and_(models.WebhookAttempt.attempted_at < webhook_cutoff, ~active_delivery)),
        (models.WebhookDelivery, and_(models.WebhookDelivery.state.in_(("delivered", "dead")),
                                     terminal_time < webhook_cutoff, ~open_delivery_job)),
        (models.WebhookDestinationEvent, models.WebhookDestinationEvent.at < now - timedelta(days=REGISTRY_EVENT_RETENTION_DAYS)),
        (models.IdempotencyKey, models.IdempotencyKey.created_at < now - timedelta(seconds=idempotency.TTL_SECONDS)),
    )
    for model, predicate in predicates:
        removed[model.__tablename__] = _remove(db, model, predicate, batch)
    window = int(now.replace(tzinfo=timezone.utc).timestamp()) // 60
    buckets = db.execute(select(RateLimitBucket.identity, RateLimitBucket.window)
        .where(RateLimitBucket.window < window - 2)
        .order_by(RateLimitBucket.window, RateLimitBucket.identity).limit(batch)).all()
    removed[RateLimitBucket.__tablename__] = 0
    if buckets:
        removed[RateLimitBucket.__tablename__] = db.execute(delete(RateLimitBucket).where(
            tuple_(RateLimitBucket.identity, RateLimitBucket.window).in_(buckets))).rowcount or 0
    expired_nonces = db.execute(select(models.Nonce.device_id, models.Nonce.nonce).where(
        models.Nonce.seen_at < now - timedelta(seconds=devicesig.NONCE_RETENTION_SECONDS))
        .order_by(models.Nonce.seen_at, models.Nonce.device_id, models.Nonce.nonce).limit(batch)).all()
    removed[models.Nonce.__tablename__] = 0
    if expired_nonces:
        removed[models.Nonce.__tablename__] = db.execute(delete(models.Nonce).where(
            tuple_(models.Nonce.device_id, models.Nonce.nonce).in_(expired_nonces))).rowcount or 0
    db.commit()
    return removed

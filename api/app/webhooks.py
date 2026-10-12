"""Outbound delivery of every outcome to the caller's registered webhook
destination: a durable outbox worked by the scheduled_jobs runner (HCA-P21a
Day 2, decision 7 and 8).

Before Day 2 the endpoint handed the POST to a FastAPI background task that
retried three times in process and gave up; a restart between the state
change and the POST lost the delivery, and nothing was signed. Now:

  Enqueue: answer, cancel and expire call enqueue() inside the transaction
  that changes the ask state. It writes one webhook_deliveries row (id
  whdl_..., the receiver's dedupe key, stable across retries) and one
  scheduled_jobs row of kind webhook due now. Nothing is sent inline.
  poll and websocket modes are recorded as a skipped webhook_attempts row
  (attempt 0), as before, so a caller's "we never got it" has an answer.

  Attempt: the job runner (app/jobs.py) claims the row under its lease and
  calls attempt_delivery(). The destination is re-checked with the ssrf
  rules at that moment and the POST is connected to one of the addresses
  the check returned, with the Host header and TLS SNI set to the
  registered host name, so a DNS answer that changed since registration
  cannot redirect the send. Redirects are not followed (a 3xx fails),
  connect timeout 3 s, one cancellable 10 s operation deadline including DNS.
  Response bodies are not consumed or decompressed.

  Signing: headers X-HCA-Delivery-Id, X-HCA-Timestamp (unix seconds),
  X-HCA-Event, X-HCA-Request-Id and X-HCA-Signature "t=<ts>,v1=<hex>"
  where v1 is HMAC-SHA256 under the destination secret over
  "<ts>.<raw body>"; the signed JSON includes delivery_id, which the
  receiver must match against X-HCA-Delivery-Id before deduplicating.. During a rotation overlap a second v1 entry under the
  previous secret follows. Receivers reject a stale timestamp (5 minutes)
  and deduplicate on the delivery id (verify_signature() below is the
  reference check, mirrored in docs/webhooks.md).

  Retry: one webhook_attempts row per POST. On failure the delivery is
  rescheduled on the schedule 1 s, 5 s, 30 s, 2 min, 10 min, 30 min, 1 h
  (up to 20 percent jitter); after the 8th failure it is dead, with a
  delivery.dead row in webhook_destination_events. A revoked destination,
  or one that holds no signing material (registered before Day 2), fails
  dead at once with the reason recorded.

Tests swap the transport through set_transport_factory() and shorten the
schedule through WEBHOOK_RETRY_SCHEDULE (comma separated seconds).
"""

import asyncio
import concurrent.futures
import threading
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import random
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select

from . import models, ssrf, webhook_destinations

log = logging.getLogger("hca.webhooks")

DEFAULT_RETRY_SCHEDULE = "1,5,30,120,600,1800,3600"
MAX_ATTEMPTS = len(DEFAULT_RETRY_SCHEDULE.split(",")) + 1
JITTER = 0.2
CONNECT_TIMEOUT_SECONDS = 3.0
TOTAL_TIMEOUT_SECONDS = 10.0
SIGNATURE_TOLERANCE_SECONDS = 300

STATE_QUEUED = "queued"
STATE_DELIVERED = "delivered"
STATE_DEAD = "dead"

# Tests swap in httpx.MockTransport here; production leaves it None and
# httpx opens real connections.
_transport_factory: Optional[Callable[[], httpx.BaseTransport]] = None
# DNS libraries expose a blocking resolver. Bound both how long this
# operation waits and how many outstanding lookups can occupy a thread.
_DNS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="hca-webhook-dns")
_DNS_SLOTS = threading.BoundedSemaphore(4)


def set_transport_factory(factory: Optional[Callable[[], httpx.BaseTransport]]) -> None:
    global _transport_factory
    _transport_factory = factory


def retry_schedule() -> List[float]:
    raw = os.environ.get("WEBHOOK_RETRY_SCHEDULE", DEFAULT_RETRY_SCHEDULE)
    out: List[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(max(0.0, float(part)))
        except ValueError:
            continue
    return out or [0.0]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def build_payload(event: str, request_id: str, state: str, answer: Optional[dict], sent_at: datetime) -> dict:
    return {
        "event": event,
        "request_id": request_id,
        "state": state,
        "answer": _json_safe(answer) if answer is not None else None,
        "sent_at": sent_at.isoformat(),
    }


# ------------------------------------------------------------------ signing

def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return mac.hexdigest()


def signature_header(secrets_now: List[str], timestamp: int, body: bytes) -> str:
    parts = [f"t={timestamp}"] + [f"v1={sign(s, timestamp, body)}" for s in secrets_now]
    return ",".join(parts)


def verify_signature(secret: str, header: str, body: bytes, *, delivery_id: Optional[str] = None,
                     now: Optional[int] = None, tolerance: int = SIGNATURE_TOLERANCE_SECONDS) -> bool:
    """Reference receiver check: parse t and every v1, recompute under the
    secret the receiver holds, accept when any v1 matches and t is within
    tolerance of now. Constant time on the digest comparison."""
    # Deduplication must use an authenticated ID. Reject a substituted
    # header even when the captured body/signature are otherwise valid.
    if not delivery_id or len(body) > 1024 * 1024:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("delivery_id") != delivery_id:
        return False
    now = int(time.time()) if now is None else now
    ts: Optional[int] = None
    sigs: List[str] = []
    for item in (header or "").split(","):
        k, _, v = item.strip().partition("=")
        if k == "t":
            if ts is not None:
                return False
            try:
                ts = int(v)
            except ValueError:
                return False
        elif k == "v1":
            sigs.append(v)
    if ts is None or not sigs or abs(now - ts) > tolerance:
        return False
    expected = sign(secret, ts, body)
    return any(hmac.compare_digest(expected, s) for s in sigs)


# ------------------------------------------------------------------ enqueue

def enqueue(db, ask: models.Ask, event: str, state: str, answer: Optional[dict], now: Optional[datetime] = None) -> Optional[models.WebhookDelivery]:
    """Add the outbox row and its job to the caller's transaction. Returns
    the delivery, or None when deliver_to is not a webhook (a skipped
    attempt row is added instead). The caller commits."""
    from . import jobs
    now = now or _now()
    deliver_to = ask.deliver_to or {}
    mode = deliver_to.get("mode")
    url = deliver_to.get("url")
    if mode != "webhook" or not url:
        db.add(models.WebhookAttempt(
            ask_id=ask.id, event=event, url=url or "", attempt=0, status_code=None, ok=False,
            error=f"skipped: deliver_to.mode is {mode!r}", attempted_at=_naive(now)))
        return None
    destination_id = deliver_to.get("destination_id")
    dest = db.get(models.WebhookDestination, destination_id) if destination_id else None
    if dest is None:
        dest = webhook_destinations.find_active_by_url(db, ask.tenant_id, ask.caller, url)
    if dest is None:
        # An ask from before decision 7 names an unregistered url: record the
        # skip rather than post unsigned.
        db.add(models.WebhookAttempt(
            ask_id=ask.id, event=event, url=url, attempt=0, status_code=None, ok=False,
            error="skipped: deliver_to.url is not a registered webhook destination", attempted_at=_naive(now)))
        return None
    delivery = models.WebhookDelivery(
        id="whdl_" + secrets.token_hex(12), tenant_id=ask.tenant_id, ask_id=ask.id, destination_id=dest.id,
        event=event, payload=build_payload(event, ask.id, state, answer, now), state=STATE_QUEUED,
        attempts=0, next_run_at=_naive(now), created_at=_naive(now),
    )
    db.add(delivery)
    jobs.schedule(db, tenant_id=ask.tenant_id, ask_id=ask.id, kind=jobs.KIND_WEBHOOK, run_at=now, now=now, ref_id=delivery.id)
    return delivery


# ------------------------------------------------------------------ attempt

def _pinned_request(url: str, checked: ssrf.CheckedDestination) -> tuple[str, dict, dict]:
    """The URL rewritten to connect to one checked address, the Host header
    naming the registered host, and the SNI extension for TLS."""
    parts = urlsplit(url)
    address = checked.addresses[0]
    ip = ipaddress.ip_address(address)
    netloc_host = f"[{address}]" if ip.version == 6 else address
    default_port = 443 if parts.scheme == "https" else 80
    netloc = netloc_host if checked.port == default_port else f"{netloc_host}:{checked.port}"
    pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    host_header = checked.host if checked.port == default_port else f"{checked.host}:{checked.port}"
    extensions = {"sni_hostname": checked.host} if parts.scheme == "https" else {}
    return pinned, {"Host": host_header}, extensions


def _resolve_bounded(url: str):
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise ssrf.DestinationRejected("DNS lookup capacity exhausted")
    try:
        future = _DNS_EXECUTOR.submit(ssrf.check_url, url)
        # Also release if cancellation happened before the thread started.
        future.add_done_callback(lambda _: slots.release())
        return future
    except Exception:
        slots.release()
        raise


async def _post_async(url: str, body: bytes, headers: dict) -> tuple[Optional[int], Optional[str]]:
    checked = await asyncio.wrap_future(_resolve_bounded(url))
    pinned, host_header, extensions = _pinned_request(url, checked)
    transport = _transport_factory() if _transport_factory else None
    timeout = httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=min(CONNECT_TIMEOUT_SECONDS, TOTAL_TIMEOUT_SECONDS))
    async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False) as client:
        req = client.build_request("POST", pinned, content=body, headers={**headers, **host_header}, extensions=extensions)
        resp = await client.send(req, stream=True)
        try:
            status = resp.status_code
            # Only the HTTP status acknowledges delivery. Never consume or
            # decompress a recipient-controlled response body; closing the
            # stream bounds both slow-drip and compressed-bomb responses.
        finally:
            await resp.aclose()
    return (status, None) if 200 <= status < 300 else (status, f"non-2xx status {status}")


def _post(url: str, body: bytes, headers: dict) -> tuple[Optional[int], Optional[str]]:
    """One cancellable POST with one deadline covering DNS, headers and close."""
    try:
        return asyncio.run(asyncio.wait_for(_post_async(url, body, headers), timeout=TOTAL_TIMEOUT_SECONDS))
    except asyncio.TimeoutError:
        return None, "total webhook operation deadline exceeded"
    except ssrf.DestinationRejected as exc:
        return None, f"destination rejected at send: {exc}"
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]


def _next_delay(attempts_done: int) -> float:
    sched = retry_schedule()
    base = sched[attempts_done - 1] if attempts_done - 1 < len(sched) else sched[-1]
    return base * (1.0 + random.uniform(0.0, JITTER)) if base > 0 else 0.0


def attempt_delivery(delivery_id: str, session_factory, now: Optional[datetime] = None, *, lease=None) -> dict:
    """Snapshot, release the connection for IO, then fence the durable result.

    Delivery is at least once: a process can die after a receiver accepts
    the POST but before recording success. The signed delivery_id remains
    stable and allows the receiver to reject that duplicate.
    """
    from . import jobs
    now = now or _now()
    started = time.monotonic()
    def clock():
        return now + timedelta(seconds=time.monotonic() - started)
    with session_factory() as db:
        jobs.guard(db, lease, clock())
        delivery = db.execute(select(models.WebhookDelivery).where(
            models.WebhookDelivery.id == delivery_id).with_for_update()).scalar_one_or_none()
        if delivery is None or delivery.state != STATE_QUEUED:
            result = {"action": "nothing_to_do", "reason": "delivery missing or already terminal"}
            jobs.complete(db, lease, result, clock())
            db.commit()
            return result
        dest = db.get(models.WebhookDestination, delivery.destination_id)
        if dest is None or dest.revoked_at is not None:
            return _mark_dead(db, delivery, dest, "destination revoked", clock(), lease=lease)
        secrets_now = webhook_destinations.signing_secrets(dest, now)
        if not secrets_now:
            return _mark_dead(db, delivery, dest, "destination has no signing material; rotate its secret", clock(), lease=lease)
        previous_attempts = int(delivery.attempts or 0)
        attempt_no = previous_attempts + 1
        ts = int(now.timestamp())
        payload = dict(delivery.payload)
        payload["delivery_id"] = delivery.id
        payload["sent_at"] = now.isoformat()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        url, ask_id, event = dest.url, delivery.ask_id, delivery.event
        headers = {"Content-Type": "application/json", "User-Agent": "HCA-Webhooks/1",
                   "X-HCA-Delivery-Id": delivery.id, "X-HCA-Timestamp": str(ts),
                   "X-HCA-Event": event, "X-HCA-Request-Id": ask_id,
                   "X-HCA-Signature": signature_header(secrets_now, ts, body)}
        db.commit()
    # No transaction or pooled connection is retained during DNS or HTTP.
    status_code, error = _post(url, body, headers)
    with session_factory() as db:
        jobs.guard(db, lease, clock())
        delivery = db.execute(select(models.WebhookDelivery).where(
            models.WebhookDelivery.id == delivery_id).with_for_update()).scalar_one_or_none()
        if delivery is None or delivery.state != STATE_QUEUED or delivery.attempts != previous_attempts:
            result = {"action": "nothing_to_do", "reason": "delivery changed during send"}
            jobs.complete(db, lease, result, clock())
            db.commit()
            return result
        dest = db.get(models.WebhookDestination, delivery.destination_id)
        ok = error is None
        finished = clock()
        db.add(models.WebhookAttempt(ask_id=ask_id, event=event, url=url, attempt=attempt_no,
                                    status_code=status_code, ok=ok, error=error,
                                    attempted_at=_naive(finished), delivery_id=delivery.id))
        delivery.attempts = attempt_no
        if ok:
            delivery.state = STATE_DELIVERED
            delivery.delivered_at = _naive(finished)
            delivery.next_run_at = None
            delivery.last_error = None
            result = {"action": "delivered", "delivery_id": delivery.id, "attempt": attempt_no, "status_code": status_code}
        else:
            delivery.last_error = error
            if attempt_no >= MAX_ATTEMPTS:
                return _mark_dead(db, delivery, dest, f"{MAX_ATTEMPTS} attempts failed; last: {error}", finished, lease=lease)
            delay = _next_delay(attempt_no)
            run_at = finished + timedelta(seconds=delay)
            delivery.next_run_at = _naive(run_at)
            jobs.schedule(db, tenant_id=delivery.tenant_id, ask_id=delivery.ask_id, kind=jobs.KIND_WEBHOOK,
                          run_at=run_at, now=finished, ref_id=delivery.id)
            result = {"action": "retry_scheduled", "delivery_id": delivery.id, "attempt": attempt_no,
                      "error": error, "next_run_in_seconds": round(delay, 3)}
        jobs.complete(db, lease, result, finished)
        db.commit()
        return result


def _mark_dead(db, delivery: models.WebhookDelivery, dest: Optional[models.WebhookDestination], reason: str,
               now: datetime, *, lease=None) -> dict:
    from . import jobs
    delivery.state = STATE_DEAD
    delivery.dead_at = _naive(now)
    delivery.next_run_at = None
    delivery.last_error = reason[:500]
    if dest is not None:
        db.add(models.WebhookDestinationEvent(
            tenant_id=delivery.tenant_id, destination_id=dest.id, action="delivery.dead", actor="system",
            detail={"delivery_id": delivery.id, "request_id": delivery.ask_id, "event": delivery.event,
                    "attempts": int(delivery.attempts or 0), "reason": reason[:500]}, at=_naive(now)))
    result = {"action": "dead", "delivery_id": delivery.id, "attempts": int(delivery.attempts or 0), "reason": reason[:200]}
    jobs.complete(db, lease, result, now)
    db.commit()
    log.warning("webhook delivery %s dead: %s", result["delivery_id"], reason)
    return result


# ------------------------------------------------------------------ reading

def deliveries_for(db, ask_id: str) -> List[dict]:
    rows = db.execute(select(models.WebhookDelivery).where(models.WebhookDelivery.ask_id == ask_id)
                      .order_by(models.WebhookDelivery.created_at.asc())).scalars().all()
    return [{"id": r.id, "event": r.event, "state": r.state, "attempts": r.attempts,
             "next_run_at": r.next_run_at, "delivered_at": r.delivered_at, "dead_at": r.dead_at,
             "last_error": r.last_error} for r in rows]

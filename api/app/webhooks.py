"""Outbound delivery of every outcome to the caller's deliver_to webhook.

Closes the loop the plan's Day 3 row names ("Webhook worker ... receives
webhook callback payload at receiver"): until this module existed, an
answer, a cancel or an expiry was written to audit_rows and published on
the SSE stream for the human surfaces, but the automation that asked
never heard back. deliver_to (CollectionRequest.deliver_to, mode plus
url) was stored on the ask and never read.

Behavior:
  Only mode == "webhook" with a non-empty url is delivered. poll and
  websocket modes are recorded as skipped (the caller reads
  GET /v1/requests/{id} itself); a websocket push is out of scope.
  One JSON POST per outcome event: request.answered, request.cancelled,
  request.expired. Body: {"event", "request_id", "state", "answer",
  "sent_at"}; answer is the AnswerRecord (None for a plain expire with
  no answer recorded). Headers X-HCA-Event and X-HCA-Request-Id carry
  the same two fields for receivers that route before parsing.
  Retries: WEBHOOK_MAX_ATTEMPTS tries (default 3) with sleeps taken from
  WEBHOOK_RETRY_DELAYS (default "1,3", seconds, one per retry). A 2xx
  ends the sequence; any other status, a connect error or a timeout
  records the attempt and moves on to the next.
  Audit: one webhook_attempts row per HTTP attempt (ask, event, url,
  attempt number, status code or error, ok flag, attempted_at) so a
  caller's "we never got it" can be answered from the table, not from
  memory.
  Sessions: the endpoint hands the work to FastAPI BackgroundTasks; the
  request's own Session is closed before background tasks run (FastAPI
  0.106+), so the worker opens its own SessionLocal for the audit rows.

No secret is involved and no signature is sent yet: a per-caller shared
signing secret is a later packet (it needs a second secret next to the
API key, minted and shown once the same way). Left as a named gap, not
quietly omitted.
"""

import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

from . import models
from .db import SessionLocal

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAYS = "1,3"
REQUEST_TIMEOUT_SECONDS = 10.0

# Tests swap in httpx.MockTransport here; production leaves it None and
# httpx opens real connections.
_transport_factory: Optional[Callable[[], httpx.BaseTransport]] = None


def set_transport_factory(factory: Optional[Callable[[], httpx.BaseTransport]]) -> None:
    global _transport_factory
    _transport_factory = factory


def _max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("WEBHOOK_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def _retry_delays() -> list[float]:
    raw = os.environ.get("WEBHOOK_RETRY_DELAYS", DEFAULT_RETRY_DELAYS).strip()
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(max(0.0, float(part)))
        except ValueError:
            continue
    return out


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


def deliver_outcome(request_id: str, event: str, state: str, answer: Optional[dict], deliver_to: Optional[dict]) -> list[dict]:
    """Deliver one outcome to the caller. Returns the list of attempt
    records written (empty when deliver_to is not a webhook). Safe to run
    as a FastAPI background task or inline from the expiry sweep."""
    deliver_to = deliver_to or {}
    mode = deliver_to.get("mode")
    url = deliver_to.get("url")

    db = SessionLocal()
    try:
        if mode != "webhook" or not url:
            row = models.WebhookAttempt(
                ask_id=request_id, event=event, url=url or "", attempt=0,
                status_code=None, ok=False,
                error=f"skipped: deliver_to.mode is {mode!r}",
                attempted_at=datetime.now(timezone.utc),
            )
            db.add(row)
            db.commit()
            return [_attempt_to_dict(row)]

        max_attempts = _max_attempts()
        delays = _retry_delays()
        written: list[dict] = []

        for attempt in range(1, max_attempts + 1):
            sent_at = datetime.now(timezone.utc)
            payload = build_payload(event, request_id, state, answer, sent_at)
            headers = {"X-HCA-Event": event, "X-HCA-Request-Id": request_id}
            status_code = None
            error = None
            ok = False
            try:
                transport = _transport_factory() if _transport_factory else None
                with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport) as client:
                    resp = client.post(url, json=payload, headers=headers)
                status_code = resp.status_code
                ok = 200 <= resp.status_code < 300
                if not ok:
                    error = f"non-2xx status {resp.status_code}"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]

            row = models.WebhookAttempt(
                ask_id=request_id, event=event, url=url, attempt=attempt,
                status_code=status_code, ok=ok, error=error, attempted_at=sent_at,
            )
            db.add(row)
            db.commit()
            written.append(_attempt_to_dict(row))

            if ok:
                break
            if attempt < max_attempts:
                delay = delays[attempt - 1] if attempt - 1 < len(delays) else (delays[-1] if delays else 0.0)
                if delay > 0:
                    time.sleep(delay)

        return written
    finally:
        db.close()


def _attempt_to_dict(row: models.WebhookAttempt) -> dict:
    return {
        "id": row.id,
        "request_id": row.ask_id,
        "event": row.event,
        "url": row.url,
        "attempt": row.attempt,
        "status_code": row.status_code,
        "ok": row.ok,
        "error": row.error,
        "attempted_at": row.attempted_at,
    }

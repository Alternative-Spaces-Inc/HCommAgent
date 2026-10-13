import json
import uuid
from sqlalchemy import Column, String, DateTime, JSON, Integer, BigInteger, Boolean, ForeignKey, UniqueConstraint, Index, Text
from sqlalchemy.orm import relationship

from .db import Base


def gen_id() -> str:
    return uuid.uuid4().hex


# HCA-P19: every record belongs to exactly one tenant. Rows that predate the
# tenants table were backfilled to this id by the P19 migration so an
# upgraded deployment keeps working as a one-tenant installation.
DEFAULT_TENANT_ID = "default"
ALL_SCOPES_JSON = '["asks:create", "asks:read", "asks:cancel", "webhooks:manage"]'


class Tenant(Base):
    """One customer organisation. Asks, devices, api keys and audit rows
    carry tenant_id; every read, stream and write is filtered on it from
    authenticated server state (the api key's or the device's tenant),
    never from a client supplied value."""
    __tablename__ = "tenants"

    id = Column(String, primary_key=True, default=gen_id)
    slug = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    disabled_at = Column(DateTime, nullable=True)


def ensure_default_tenant(db) -> "Tenant":
    """Idempotent: the P19 migration inserts the default tenant for upgraded
    deployments; a database built from metadata (tests, a fresh local run
    without alembic) gets it here, at app startup."""
    from datetime import datetime, timezone
    t = db.get(Tenant, DEFAULT_TENANT_ID)
    if t is None:
        t = Tenant(id=DEFAULT_TENANT_ID, slug=DEFAULT_TENANT_ID, name="Default tenant", created_at=datetime.now(timezone.utc))
        db.add(t)
        db.commit()
    return t


class TenantOwner(Base):
    """A Google account allowed to link a device of this tenant
    (app/googleauth.py). Replaces the deployment wide HCA_GOOGLE_OWNERS
    list, which stays as a fallback for the default tenant only."""
    __tablename__ = "tenant_owners"

    id = Column(String, primary_key=True, default=gen_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    removed_at = Column(DateTime, nullable=True)


class Ask(Base):
    """A CollectionRequest plus its RequestView tracking fields."""
    __tablename__ = "asks"

    id = Column(String, primary_key=True, default=gen_id)
    caller = Column(String, nullable=False)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, default=DEFAULT_TENANT_ID, server_default=DEFAULT_TENANT_ID, index=True)
    audience = Column(String, nullable=False)
    title = Column(String, nullable=False)
    why = Column(String, nullable=True)
    path_hint = Column(JSON, nullable=True)
    zoom = Column(JSON, nullable=True)
    control = Column(String, nullable=False)
    suggested = Column(JSON, nullable=False)
    red_gate = Column(JSON, nullable=True)
    voice = Column(JSON, nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    expiry_policy = Column(String, nullable=False, default="auto_deny")
    deliver_to = Column(JSON, nullable=False)

    state = Column(String, nullable=False, default="queued")
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=True)

    options = relationship("AskOption", back_populates="ask", cascade="all, delete-orphan")
    deliveries = relationship("AskDelivery", back_populates="ask", cascade="all, delete-orphan")
    answer = relationship("AuditRow", back_populates="ask", uselist=False)


class AskOption(Base):
    """One Option from CollectionRequest.options_or_fields."""
    __tablename__ = "options"

    id = Column(String, primary_key=True, default=gen_id)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False)
    option_id = Column(String, nullable=False)
    label = Column(String, nullable=False)
    description = Column(String, nullable=True)
    position = Column(Integer, nullable=False, default=0)

    ask = relationship("Ask", back_populates="options")


class AskDelivery(Base):
    """One entry from CollectionRequest.deliveries."""
    __tablename__ = "deliveries"

    id = Column(String, primary_key=True, default=gen_id)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False)
    surface = Column(String, nullable=False)
    delivered_at = Column(DateTime, nullable=False)
    state = Column(String, nullable=False)

    ask = relationship("Ask", back_populates="deliveries")


class ApiKey(Base):
    """Caller credentials, named in the plan's Day 4 auth work; stored now so
    later migrations do not need a new table."""
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=gen_id)
    caller = Column(String, nullable=False, unique=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, default=DEFAULT_TENANT_ID, server_default=DEFAULT_TENANT_ID, index=True)
    key_prefix = Column(String, nullable=True, unique=True)
    key_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    # HCA-P21b: fixed scope vocabulary (apikeys.SCOPES) stored as a JSON list;
    # the server default backfills pre-P21b rows with every scope so existing
    # callers keep working.
    scopes = Column(Text, nullable=False, server_default=ALL_SCOPES_JSON, default=ALL_SCOPES_JSON)

    @property
    def scope_list(self) -> list:
        try:
            value = json.loads(self.scopes or "[]")
        except ValueError:
            return []
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


class ApiKeyEvent(Base):
    """HCA-P21b audit trail for key lifecycle changes (issued, scopes_changed,
    revoked), written in the same transaction as the change."""
    __tablename__ = "api_key_events"
    __table_args__ = (Index("ix_api_key_events_key_at", "api_key_id", "at"),)
    id = Column(String, primary_key=True, default=gen_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    api_key_id = Column(String, ForeignKey("api_keys.id"), nullable=False)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    at = Column(DateTime, nullable=False)


class AuditRow(Base):
    """An AnswerRecord, one per answered ask, plus the audit fields GET /v1/audit needs."""
    __tablename__ = "audit_rows"

    id = Column(String, primary_key=True, default=gen_id)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False, unique=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, default=DEFAULT_TENANT_ID, server_default=DEFAULT_TENANT_ID, index=True)
    control = Column(String, nullable=False)
    value = Column(JSON, nullable=True)
    answered_by = Column(String, nullable=True)
    answered_role = Column(String, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    surface = Column(String, nullable=False)
    device = Column(String, nullable=True)
    path_taken = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    voice_used = Column(Boolean, nullable=True)
    passthrough = Column(JSON, nullable=True)

    ask = relationship("Ask", back_populates="answer")


class IdempotencyKey(Base):
    """HCA-P19 Day 2 (app/idempotency.py): one stored response per
    (tenant, scope, route, key) so a retried POST replays instead of
    repeating its effect."""
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "scope", "route", "key", name="uq_idempotency_scope_key"),)

    id = Column(String, primary_key=True, default=gen_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    scope = Column(String, nullable=False)
    route = Column(String, nullable=False)
    key = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status_code = Column(Integer, nullable=False)
    response = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True)


class Event(Base):
    """HCA-P20 Day 1 (app/events.py): one row per inbox stream envelope.
    publish() writes the row before any in-process delivery, so an envelope
    survives the instance that produced it; a reconnecting stream sends the
    last id it saw (SSE Last-Event-ID) and gets the rows it missed, within
    the replay window, before live events resume. The id is the database's
    own sequence, so it is monotonic per table and usable as the SSE id.
    device_ids null means every device of the tenant (terminal events);
    otherwise the server side routing set of a created or escalated ask."""
    __tablename__ = "events"
    # SQLite needs the explicit AUTOINCREMENT keyword to preserve cursor
    # monotonicity even after retention deletes every event row.
    __table_args__ = (Index("ix_events_tenant_id_id", "tenant_id", "id"), {"sqlite_autoincrement": True})

    id = Column(Integer().with_variant(BigInteger(), "postgresql"), primary_key=True, autoincrement=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    event = Column(String, nullable=False)
    data = Column(JSON, nullable=False)
    device_ids = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, index=True)


class ScheduledJob(Base):
    """HCA-P20 Day 3 (app/jobs.py): one row per timed action on an ask, the
    escalation of a later rank (kind escalate, run_at = the rank's window
    open) or the expiry (kind expire, run_at = expires_at). Any instance's
    worker may claim a due row: the claim is a compare-and-set on
    lease_expires_at (FOR UPDATE SKIP LOCKED on Postgres so claimants do
    not queue on each other), so each job runs once however many instances
    poll. Replaces the in-process asyncio timers (P14) and the sweep's
    unleased scan (P19)."""
    __tablename__ = "scheduled_jobs"

    id = Column(String, primary_key=True, default=gen_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)
    run_at = Column(DateTime, nullable=False, index=True)
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    done_at = Column(DateTime, nullable=True, index=True)
    result = Column(JSON, nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    # HCA-P21a Day 2: the row a non ask job points at (kind webhook: the
    # webhook_deliveries id). Null for escalate and expire.
    ref_id = Column(String, nullable=True, index=True)


class Nonce(Base):
    """HCA-P20 Day 2 (app/nonces.py): one row per (device, nonce) seen inside
    the signature skew window, so a signed request replayed against another
    API instance, or after a restart, is refused like one replayed against
    the same process. Rows older than the window are pruned on the way in."""
    __tablename__ = "nonces"

    device_id = Column(String, primary_key=True)
    nonce = Column(String, primary_key=True)
    seen_at = Column(DateTime, nullable=False, index=True)


class WebhookDestination(Base):
    """HCA-P21a: a webhook URL a caller registered once. Asks may only name a
    registered, non revoked destination of the same caller and tenant
    (decision 7). secret_hash is hmac256$<hex> under the api key pepper, the
    secret itself is returned once; previous_secret_hash keeps the prior
    secret valid for 24 h after a rotation so the receiver can switch.
    pinned_addresses are the addresses the URL resolved to at registration;
    the send re-resolves and re-checks (app/ssrf.py)."""
    __tablename__ = "webhook_destinations"

    id = Column(String, primary_key=True, default=gen_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    caller = Column(String, nullable=False, index=True)
    url = Column(String, nullable=False)
    secret_prefix = Column(String, nullable=False)
    secret_hash = Column(String, nullable=False)
    previous_secret_hash = Column(String, nullable=True)
    previous_valid_until = Column(DateTime, nullable=True)
    pinned_addresses = Column(JSON, nullable=True)
    # HCA-P21a Day 2 (decision 8): the secrets encrypted under a key derived
    # from the api key pepper (app/webhook_crypto.py) so deliveries can be
    # signed. Null on a destination registered on Day 1 (rotate to fix).
    secret_enc = Column(String, nullable=True)
    previous_secret_enc = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    rotated_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class WebhookDestinationEvent(Base):
    """HCA-P21a audit trail of the destination registry: one row per
    registration, rotation and revocation, with the acting caller."""
    __tablename__ = "webhook_destination_events"

    id = Column(String, primary_key=True, default=gen_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    destination_id = Column(String, ForeignKey("webhook_destinations.id"), nullable=False, index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    detail = Column(JSON, nullable=True)
    at = Column(DateTime, nullable=False)


class WebhookDelivery(Base):
    """HCA-P21a Day 2: the durable outbox. One row per outcome owed to a
    registered destination (answer, cancel, expire), written in the same
    transaction as the state change, worked by scheduled_jobs rows of kind
    webhook (app/webhooks.py). state: queued, delivered, dead. The id is
    the X-HCA-Delivery-Id a receiver deduplicates on; it is stable across
    retries."""
    __tablename__ = "webhook_deliveries"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False, index=True)
    destination_id = Column(String, ForeignKey("webhook_destinations.id"), nullable=False, index=True)
    event = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    state = Column(String, nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    dead_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)


class WebhookAttempt(Base):
    """One HTTP attempt to deliver an outcome to the caller's deliver_to
    webhook (app/webhooks.py). attempt 0 with ok False records a skip
    (deliver_to was not a webhook); attempts 1..N record real POSTs."""
    __tablename__ = "webhook_attempts"

    id = Column(String, primary_key=True, default=gen_id)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False, index=True)
    event = Column(String, nullable=False)
    url = Column(String, nullable=False)
    attempt = Column(Integer, nullable=False)
    status_code = Column(Integer, nullable=True)
    ok = Column(Boolean, nullable=False, default=False)
    error = Column(String, nullable=True)
    attempted_at = Column(DateTime, nullable=False)
    # HCA-P21a Day 2: the outbox row this attempt served (null for skips and
    # rows from before the outbox).
    delivery_id = Column(String, nullable=True, index=True)


class Device(Base):
    """A registered human surface device (HCA-P6, app/devicesig.py). The id
    is derived from the public key, so the same key always registers as the
    same device and a device cannot be registered under two ids."""
    __tablename__ = "devices"

    id = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, default=DEFAULT_TENANT_ID, server_default=DEFAULT_TENANT_ID, index=True)
    surface = Column(String, nullable=False, default="browser_overlay")
    # HCA-P14 (app/routing.py): lower is earlier in the surface ladder.
    # Devices with the same value share a rank and see an ask together; the
    # default keeps every device at rank 0, which is the pre-P14 behaviour.
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    public_key_pem = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    # HCA-P7: the Google account linked to this device through
    # POST /v1/devices/self/google-link (app/googleauth.py). Null until the
    # owner signs in from the options page.
    google_sub = Column(String, nullable=True)
    google_email = Column(String, nullable=True)
    google_linked_at = Column(DateTime, nullable=True)

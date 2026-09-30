import uuid
from sqlalchemy import Column, String, DateTime, JSON, Integer, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from .db import Base


def gen_id() -> str:
    return uuid.uuid4().hex


class Ask(Base):
    """A CollectionRequest plus its RequestView tracking fields."""
    __tablename__ = "asks"

    id = Column(String, primary_key=True, default=gen_id)
    caller = Column(String, nullable=False)
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
    key_prefix = Column(String, nullable=True, unique=True)
    key_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class AuditRow(Base):
    """An AnswerRecord, one per answered ask, plus the audit fields GET /v1/audit needs."""
    __tablename__ = "audit_rows"

    id = Column(String, primary_key=True, default=gen_id)
    ask_id = Column(String, ForeignKey("asks.id"), nullable=False, unique=True)
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

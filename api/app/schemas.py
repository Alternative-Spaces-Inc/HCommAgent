from datetime import datetime
from typing import Any, List, Optional, Literal

from pydantic import BaseModel, Field

Surface = Literal["browser_overlay", "browser_notification", "macos_notification", "macos_app", "web"]
RequestState = Literal["queued", "shown", "answered", "expired", "withdrawn", "cancelled"]
Control = Literal["radio", "checkbox", "approve_deny", "dropdown", "chat", "confirm_rows"]


class PathHint(BaseModel):
    route: Optional[str] = None
    screen: Optional[str] = None
    control_label: Optional[str] = None
    button_label: Optional[str] = None


class Option(BaseModel):
    id: str
    label: str
    description: Optional[str] = None


class Suggested(BaseModel):
    option_id: Optional[str] = None
    why: Optional[str] = None


class RedGate(BaseModel):
    resource: Optional[str] = None
    change: Optional[str] = None
    impact: Optional[str] = None
    exact_command: Optional[str] = None
    rollback: Optional[str] = None


class Voice(BaseModel):
    read_aloud: bool = False
    accept_speech: bool = False


class Delivery(BaseModel):
    surface: Surface
    delivered_at: datetime
    state: str


class DeliverTo(BaseModel):
    mode: Optional[Literal["webhook", "poll", "websocket"]] = None
    url: Optional[str] = None


class CollectionRequest(BaseModel):
    caller: str
    audience: str
    title: str
    why: Optional[str] = None
    path_hint: Optional[PathHint] = None
    zoom: Optional[dict] = None
    control: Control
    options_or_fields: List[Option]
    suggested: Suggested
    red_gate: Optional[RedGate] = None
    voice: Optional[Voice] = None
    deadline_at: Optional[datetime] = None
    expiry_policy: Literal["auto_deny", "expire", "hold"] = "auto_deny"
    deliveries: Optional[List[Delivery]] = None
    deliver_to: DeliverTo


class RequestReceipt(BaseModel):
    request_id: str
    state: RequestState


class AnswerSubmission(BaseModel):
    control: str
    value: Any
    answered_by: Optional[str] = None
    answered_role: Optional[str] = None
    surface: Surface
    device: Optional[str] = None
    path_taken: Optional[List[str]] = None
    duration_ms: Optional[int] = None
    voice_used: Optional[bool] = None
    passthrough: Optional[dict] = None


class AnswerRecord(BaseModel):
    request_id: str
    control: str
    value: Any = None
    answered_by: Optional[str] = None
    answered_role: Optional[str] = None
    answered_at: Optional[datetime] = None
    surface: Surface
    device: Optional[str] = None
    path_taken: Optional[List[str]] = None
    duration_ms: Optional[int] = None
    voice_used: Optional[bool] = None
    passthrough: Optional[dict] = None


class RequestView(BaseModel):
    request_id: str
    state: RequestState
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    request: CollectionRequest
    answer: Optional[AnswerRecord] = None

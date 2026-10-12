from datetime import datetime
from typing import Any, ClassVar, List, Optional, Literal, get_args

from pydantic import BaseModel, Field, ConfigDict, field_validator
import math
from .json_dates import UTCJSONModel

Surface = Literal["browser_overlay", "browser_notification", "macos_notification", "macos_app", "web"]
RequestState = Literal["queued", "shown", "answered", "expired", "withdrawn", "cancelled"]
Control = Literal["radio", "checkbox", "approve_deny", "dropdown", "chat", "confirm_rows"]


def _contains_typed_model(annotation):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    return any(_contains_typed_model(argument) for argument in get_args(annotation))


class BoundedModel(UTCJSONModel):
    model_config = ConfigDict(str_max_length=4000, extra="forbid")
    JSON_POLICY: ClassVar[dict] = {
        "maxDepth": 12, "maxNodesPerField": 10000, "maxStringLength": 4000,
        "maxObjectKeyLength": 100, "finiteNumbersOnly": True,
    }

    @field_validator("*", mode="before")
    @classmethod
    def bounded_json(cls, value, info):
        # A typed nested model validates each of its own fields. Recounting
        # its complete serialized dictionary would incorrectly charge the
        # enclosing RequestView for independent request/answer fields.
        if _contains_typed_model(cls.model_fields[info.field_name].annotation):
            return value
        policy = cls.JSON_POLICY
        count = 0
        def walk(item, depth):
            nonlocal count
            count += 1
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("non-finite numbers are not permitted")
            if depth > policy["maxDepth"] or count > policy["maxNodesPerField"]:
                raise ValueError("nested data exceeds allowed complexity")
            if isinstance(item, str) and len(item) > policy["maxStringLength"]:
                raise ValueError("nested string exceeds 4000 characters")
            if isinstance(item, dict):
                for key, child in item.items():
                    if len(str(key)) > policy["maxObjectKeyLength"]:
                        raise ValueError("field name exceeds 100 characters")
                    walk(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    walk(child, depth + 1)
        if isinstance(value, (dict, list)):
            walk(value, 0)
        return value


class PathHint(BoundedModel):
    route: Optional[str] = None
    screen: Optional[str] = None
    control_label: Optional[str] = None
    button_label: Optional[str] = None


class Option(BoundedModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)


class Suggested(BoundedModel):
    option_id: Optional[str] = None
    why: Optional[str] = Field(default=None, max_length=2000)


class RedGate(BoundedModel):
    resource: Optional[str] = None
    change: Optional[str] = None
    impact: Optional[str] = None
    exact_command: Optional[str] = None
    rollback: Optional[str] = None


class Voice(BoundedModel):
    read_aloud: bool = False
    accept_speech: bool = False


class Delivery(BoundedModel):
    surface: Surface
    delivered_at: datetime
    state: str


class DeliverTo(BoundedModel):
    mode: Optional[Literal["webhook", "poll", "websocket"]] = None
    url: Optional[str] = Field(default=None, max_length=2048)
    # HCA-P21a: set by the server from the registry when mode is webhook; a
    # value sent by the caller is ignored.
    destination_id: Optional[str] = None


class CollectionRequest(BoundedModel):
    caller: str = Field(min_length=1, max_length=100)
    audience: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    why: Optional[str] = Field(default=None, max_length=2000)
    path_hint: Optional[PathHint] = None
    zoom: Optional[dict] = None
    control: Control
    options_or_fields: List[Option] = Field(max_length=500)
    suggested: Suggested
    red_gate: Optional[RedGate] = None
    voice: Optional[Voice] = None
    deadline_at: Optional[datetime] = None
    expiry_policy: Literal["auto_deny", "expire", "hold"] = "auto_deny"
    deliveries: Optional[List[Delivery]] = Field(default=None, max_length=50)
    deliver_to: DeliverTo


class InboxItem(CollectionRequest):
    request_id: str
    expires_at: Optional[datetime] = None


class RequestReceipt(BoundedModel):
    request_id: str
    state: RequestState


class AnswerSubmission(BoundedModel):
    control: str
    value: Any
    answered_by: Optional[str] = None
    answered_role: Optional[str] = None
    surface: Surface
    device: Optional[str] = None
    path_taken: Optional[List[str]] = Field(default=None, max_length=30)
    duration_ms: Optional[int] = Field(default=None, ge=0, le=2592000000)
    voice_used: Optional[bool] = None
    passthrough: Optional[dict] = None


class AnswerRecord(BoundedModel):
    # Human audit wrapping adds exactly two levels and nine nodes to the
    # accepted passthrough field. Input limits remain on AnswerSubmission.
    JSON_POLICY: ClassVar[dict] = {**BoundedModel.JSON_POLICY, "maxDepth": 14, "maxNodesPerField": 10009}
    request_id: str
    control: str
    value: Any = None
    answered_by: Optional[str] = None
    answered_role: Optional[str] = None
    answered_at: Optional[datetime] = None
    surface: Surface
    device: Optional[str] = None
    path_taken: Optional[List[str]] = Field(default=None, max_length=30)
    duration_ms: Optional[int] = Field(default=None, ge=0, le=2592000000)
    voice_used: Optional[bool] = None
    passthrough: Optional[dict] = None


class RequestView(BoundedModel):
    request_id: str
    state: RequestState
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    request: CollectionRequest
    answer: Optional[AnswerRecord] = None


class CancelRequestBody(BoundedModel):
    reason: str = Field(min_length=1, max_length=2000)

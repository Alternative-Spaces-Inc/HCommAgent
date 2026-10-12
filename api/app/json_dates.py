"""Serialize the database's UTC timestamps as RFC 3339 without changing storage."""
from datetime import datetime, timezone

from pydantic import BaseModel, field_serializer


def utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class UTCJSONModel(BaseModel):
    @field_serializer("*", mode="wrap", when_used="json", check_fields=False)
    def serialize_utc_datetime(self, value, handler, info):
        if (info.context or {}).get("preserve_datetime_input"):
            return handler(value)
        return handler(utc_datetime(value) if isinstance(value, datetime) else value)

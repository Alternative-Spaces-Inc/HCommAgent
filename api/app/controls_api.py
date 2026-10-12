"""GET /v1/controls: the control library, machine readable (review item 25
closes the parity exception that documented this route without serving it).

Public and static: it describes request and answer shapes so an AI caller can
build an ask without reading the source. Nothing tenant or caller specific is
returned. The client capability matrix (which surface renders which control)
lives in docs/STATUS.md; the API accepts every control listed here, a surface
that cannot render one shows a fallback card and the ask waits for a surface
that can.
"""
from typing import Any

from fastapi import APIRouter

from . import controls

router = APIRouter(prefix="/v1", tags=["controls"])

_OPTION_SCHEMA = {
    "type": "object",
    "required": ["id", "label"],
    "properties": {
        "id": {"type": "string", "maxLength": controls.MAX_OPTION_ID},
        "label": {"type": "string", "maxLength": controls.MAX_LABEL},
        "description": {"type": "string", "maxLength": controls.MAX_DESCRIPTION},
    },
}

_EXAMPLES: dict[str, dict[str, Any]] = {
    "radio": {"options": [{"id": "keep", "label": "Keep the current price"}, {"id": "lower", "label": "Lower by 5 percent"}], "answer": "lower"},
    "dropdown": {"options": [{"id": f"size-{s}", "label": s.upper()} for s in ("xs", "s", "m", "l", "xl")], "answer": "size-m"},
    "approve_deny": {"options": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}], "answer": "approve"},
    "checkbox": {"options": [{"id": "web", "label": "Web"}, {"id": "email", "label": "Email"}, {"id": "store", "label": "In store"}], "answer": ["web", "email"]},
    "chat": {"options": [], "answer": "a1b2c3"},
    "confirm_rows": {"options": [{"id": "row-1", "label": "SKU 1001 price 19.00"}, {"id": "row-2", "label": "SKU 1002 price 24.00"}], "answer": ["row-1", "row-2"]},
}


def _answer_schema(control: str, spec: dict[str, Any]) -> dict[str, Any]:
    if control in ("checkbox", "confirm_rows"):
        return {"type": "array", "items": {"type": "string"}, "uniqueItems": True,
                "minItems": 1 if control == "confirm_rows" else 0, "description": spec["value"]}
    if control == "chat":
        return {"type": "string", "minLength": 1, "maxLength": controls.MAX_CHAT, "description": spec["value"]}
    return {"type": "string", "description": spec["value"]}


def control_specs() -> list[dict[str, Any]]:
    out = []
    for name, spec in controls.CONTROLS.items():
        request_schema = {
            "type": "object",
            "required": ["control", "title", "options"],
            "properties": {
                "control": {"const": name},
                "title": {"type": "string", "maxLength": controls.MAX_TITLE},
                "why": {"type": "string", "maxLength": controls.MAX_WHY},
                "options": {"type": "array", "items": _OPTION_SCHEMA,
                            "minItems": spec["min_options"], "maxItems": spec["max_options"]},
            },
            "description": spec["summary"],
        }
        example = _EXAMPLES[name]
        out.append({
            "control": name,
            "request_schema": request_schema,
            "answer_schema": _answer_schema(name, spec),
            "example": {"request": {"control": name, "title": "Example", "options": example["options"]},
                        "answer": {"value": example["answer"]}},
        })
    return out


@router.get("/controls")
def list_controls() -> list[dict[str, Any]]:
    return control_specs()

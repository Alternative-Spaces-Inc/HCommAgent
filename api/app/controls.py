"""HCA-P19 Day 2: the control catalogue and strict value validation.

One place says what each control is, what an ask must carry to use it,
and what an answer value must look like. GET /v1/controls (Day 3) serves
the same catalogue to clients, so the server, the contract and the UI
agree by construction.

Create time (validate_request_shape): option ids unique and non empty,
counts within the control's bounds, sizes bounded. Answer time
(validate_answer_value): the value fits the control and the ask's own
option ids exactly; anything else is 422 with a reason a client can show.
"""

from typing import Any, Iterable, List, Optional

MAX_OPTIONS = 50
MAX_TITLE = 300
MAX_WHY = 2000
MAX_LABEL = 200
MAX_DESCRIPTION = 1000
MAX_OPTION_ID = 100
MAX_CALLER = 100
MAX_CHAT = 4000
MAX_ROWS = 500

CONTROLS: dict[str, dict[str, Any]] = {
    "radio": {
        "summary": "One option from a list (a single option is allowed: the ask then reads as a confirm).",
        "min_options": 1, "max_options": MAX_OPTIONS,
        "value": "string: one option id",
    },
    "dropdown": {
        "summary": "One option from a longer list, shown as a select.",
        "min_options": 1, "max_options": MAX_OPTIONS,
        "value": "string: one option id",
    },
    "approve_deny": {
        "summary": "Two options, approve or deny; the red gate control.",
        "min_options": 2, "max_options": 2,
        "value": "string: one of the two option ids",
    },
    "checkbox": {
        "summary": "Any subset of the options, empty allowed.",
        "min_options": 1, "max_options": MAX_OPTIONS,
        "value": "array of distinct option ids",
    },
    "chat": {
        "summary": "Free text, only where no option can carry the answer (an id or hash).",
        "min_options": 0, "max_options": 0,
        "value": f"non empty string, at most {MAX_CHAT} characters",
    },
    "confirm_rows": {
        "summary": "Confirm a list of rows by id.",
        "min_options": 1, "max_options": MAX_ROWS,
        "value": "array of distinct row ids, each an option id, at least one",
    },
}


class ControlError(ValueError):
    """A shape problem a client can display; mapped to HTTP 422 by the routes."""


def _ids(option_ids: Iterable[str]) -> List[str]:
    return list(option_ids)


def validate_request_shape(control: str, options: list, title: str, why: Optional[str], caller: str) -> None:
    spec = CONTROLS.get(control)
    if spec is None:
        raise ControlError(f"unknown control {control!r}")
    if not title or len(title) > MAX_TITLE:
        raise ControlError(f"title must be 1 to {MAX_TITLE} characters")
    if why is not None and len(why) > MAX_WHY:
        raise ControlError(f"why must be at most {MAX_WHY} characters")
    if not caller or len(caller) > MAX_CALLER:
        raise ControlError(f"caller must be 1 to {MAX_CALLER} characters")
    n = len(options)
    if n < spec["min_options"] or n > spec["max_options"]:
        raise ControlError(f"{control} needs {spec['min_options']} to {spec['max_options']} options, got {n}")
    seen = set()
    for o in options:
        oid = (o.id or "").strip()
        if not oid or len(oid) > MAX_OPTION_ID:
            raise ControlError(f"option ids must be 1 to {MAX_OPTION_ID} characters")
        if oid in seen:
            raise ControlError(f"duplicate option id {oid!r}")
        seen.add(oid)
        if not (o.label or "").strip() or len(o.label) > MAX_LABEL:
            raise ControlError(f"option labels must be 1 to {MAX_LABEL} characters")
        if o.description is not None and len(o.description) > MAX_DESCRIPTION:
            raise ControlError(f"option descriptions must be at most {MAX_DESCRIPTION} characters")


def validate_answer_value(control: str, value: Any, option_ids: Iterable[str]) -> Any:
    """Returns the normalised value or raises ControlError."""
    ids = set(_ids(option_ids))
    if control in ("radio", "dropdown", "approve_deny"):
        if not isinstance(value, str) or not value:
            raise ControlError(f"{control} value must be one option id as a string")
        if value not in ids:
            raise ControlError(f"{control} value {value!r} is not one of the request's option ids")
        return value
    if control == "checkbox":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ControlError("checkbox value must be an array of option id strings")
        if len(set(value)) != len(value):
            raise ControlError("checkbox value repeats an option id")
        unknown = [v for v in value if v not in ids]
        if unknown:
            raise ControlError(f"checkbox value names unknown option ids {unknown!r}")
        return value
    if control == "chat":
        if not isinstance(value, str) or not value.strip():
            raise ControlError("chat value must be a non empty string")
        if len(value) > MAX_CHAT:
            raise ControlError(f"chat value must be at most {MAX_CHAT} characters")
        return value
    if control == "confirm_rows":
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise ControlError("confirm_rows value must be a non empty array of row id strings")
        if len(set(value)) != len(value):
            raise ControlError("confirm_rows value repeats a row id")
        unknown = [v for v in value if v not in ids]
        if unknown:
            raise ControlError(f"confirm_rows value names unknown row ids {unknown!r}")
        return value
    raise ControlError(f"unknown control {control!r}")

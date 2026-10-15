"""Contract metadata for behavior that FastAPI cannot infer from route types.

The served schema and the checked-in export use this same definition. Keep
runtime validation in the route/model; describe cross-field policies here
when JSON Schema cannot express them (for example, an ask's allowed option IDs).
"""
from copy import deepcopy

from . import controls, devicesig, idempotency
from .schemas import BoundedModel
from .middleware import MAX_BODY_BYTES

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
IDEMPOTENT_ROUTES = {
    ("/v1/requests", "post"),
    ("/v1/requests/{request_id}/answer", "post"),
    ("/v1/requests/{request_id}/cancel", "post"),
}
DEVICE_HEADERS = {
    "DeviceID": ("X-HCA-Device", 100, "Registered device ID"),
    "DeviceTimestamp": ("X-HCA-Timestamp", 16, "Unix seconds within 300 seconds of server time"),
    "DeviceNonce": ("X-HCA-Nonce", 128, "Fresh random nonce; replayed nonces are refused"),
    "DeviceSignature": ("X-HCA-Signature", 100, "Base64url raw P-256 ECDSA signature over the canonical HCA1 request"),
}
ERRORS = {
    "400": "Invalid operation or webhook destination",
    "401": "Missing, invalid, stale or replayed authentication",
    "403": "Credential, owner membership, tenant or configured authorization refused",
    "404": "Resource not found within the authenticated caller or tenant",
    "409": "Conflicting resource state or incomplete idempotent operation",
    "413": "Request body exceeds 1 MiB",
    "422": "Invalid input, control value, deadline or idempotency key reuse",
    "429": "Shared request budget, api key ceiling or tenant quota reached",
    "503": "Required database or Google verification configuration unavailable",
}
OPERATION_ERRORS = {
    ("/v1/requests", "post"): {"400", "409", "422"},
    ("/v1/requests/{request_id}", "get"): {"404"},
    ("/v1/requests/{request_id}/answer", "post"): {"404", "409", "422"},
    ("/v1/requests/{request_id}/cancel", "post"): {"404", "409", "422"},
    ("/v1/devices", "post"): {"409", "422"},
    ("/v1/devices/{device_id}/revoke", "post"): {"404"},
    ("/v1/devices/{device_id}", "patch"): {"404", "422"},
    ("/v1/devices/self/rotate", "post"): {"409", "422"},
    ("/v1/devices/self/google-link", "post"): {"422", "503"},
    ("/v1/tenants", "post"): {"409", "422"},
    ("/v1/tenants/{tenant_id}", "get"): {"404"},
    ("/v1/tenants/{tenant_id}/disable", "post"): {"404", "409"},
    ("/v1/tenants/{tenant_id}", "patch"): {"404", "409", "422"},
    ("/v1/tenants/{tenant_id}/owners", "post"): {"404", "422"},
    ("/v1/tenants/{tenant_id}/owners/{email}", "delete"): {"404"},
    ("/v1/tenants/{tenant_id}/records", "get"): {"404"},
    ("/v1/tenants/{tenant_id}/records/purge", "post"): {"404", "422"},
    ("/v1/api-keys", "post"): {"422"},
    ("/v1/api-keys/{prefix}/revoke", "post"): {"404"},
    ("/v1/api-keys/{prefix}", "patch"): {"404", "409", "422"},
    ("/v1/api-keys/{prefix}/events", "get"): {"404"},
    ("/v1/api-keys/{prefix}/rotate", "post"): {"404", "409"},
    ("/v1/api-keys/self/rotate", "post"): {"409"},
    ("/v1/webhooks", "post"): {"400", "422"},
    ("/v1/webhooks/{destination_id}/rotate", "post"): {"404", "409"},
    ("/v1/webhooks/{destination_id}", "delete"): {"404"},
    ("/health", "get"): {"503"},
    ("/healthz", "get"): {"503"},
}


def _string_bounds(node, maximum):
    if isinstance(node, dict):
        if node.get("type") == "string" and "format" not in node:
            node.setdefault("maxLength", maximum)
        for child in node.values():
            _string_bounds(child, maximum)
    elif isinstance(node, list):
        for child in node:
            _string_bounds(child, maximum)


def enrich_schema(generated):
    schema = deepcopy(generated)
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    security = schema["components"].setdefault("securitySchemes", {})
    schemas["HTTPError"] = {
        "type": "object", "required": ["detail"], "properties": {"detail": {}},
        "description": "HTTP errors have detail as text, a terminal-state object or a redacted validation list.",
    }
    schemas["HealthDegraded"] = {
        "type": "object", "required": ["status", "db"],
        "properties": {"status": {"const": "degraded"}, "db": {"const": "down"}},
    }
    schemas["SweepResult"] = {
        "type": "object", "required": ["swept", "results", "idempotency_swept", "jobs_run", "reconciled", "retention"],
        "properties": {
            "swept": {"type": "integer"}, "idempotency_swept": {"type": "integer"},
            "jobs_run": {"type": "integer"}, "reconciled": {"type": "integer"},
            "retention": {"type": "object", "additionalProperties": {"type": "integer"}},
            "results": {"type": "array", "items": {
                "type": "object", "required": ["request_id", "expiry_policy", "action"],
                "properties": {"request_id": {"type": "string"},
                    "expiry_policy": {"type": ["string", "null"]}, "action": {"type": ["string", "null"]}},
            }},
        },
    }
    for name, (header, maximum, description) in DEVICE_HEADERS.items():
        security[name] = {"type": "apiKey", "in": "header", "name": header, "description": description}
    security["DeviceSignature"]["description"] += (
        " All four device headers are required together. Canonical fields joined by newline: "
        "HCA1, timestamp, nonce, METHOD, path-and-query, SHA256(body). "
        f"Timestamp skew is at most {devicesig.MAX_SKEW_SECONDS} seconds; nonce retention is "
        f"{devicesig.NONCE_RETENTION_SECONDS} seconds. Production human decision routes also require current Google owner membership."
    )
    security.setdefault("AgentKey", {"type": "http", "scheme": "bearer"})["description"] = (
        "Agent api key hca_live_<prefix>_<secret> issued under the admin bearer. Each key carries a subset of the "
        "fixed scopes asks:create (POST /v1/requests), asks:read (GET /v1/requests/{request_id}), asks:cancel "
        "(POST /v1/requests/{request_id}/cancel) and webhooks:manage (every /v1/webhooks route); omitted at issuance "
        "means all four. A key lacking the route's scope gets 403 with detail 'api key lacks scope <name>' and a "
        "WWW-Authenticate: Bearer scope=\"<name>\" challenge. Credentials are never accepted in a query string. Every key "
        "carries rate_per_minute (default 600, every authenticated call) and asks_per_minute (default 60, POST /v1/requests); "
        "the tenant may add asks_per_day. Over a ceiling answers 429 with Retry-After; successful calls carry X-RateLimit-Limit "
        "and X-RateLimit-Remaining."
    )
    security["SweepBearer"] = {"type": "http", "scheme": "bearer",
        "description": "SWEEP_TRIGGER_TOKEN used only by the internal scheduler; missing server configuration fails closed."}

    bounded_models = {cls.__name__: cls for cls in BoundedModel.__subclasses__()}
    from .schemas import InboxItem
    bounded_models["InboxItem"] = InboxItem
    for name, model in bounded_models.items():
        if name in schemas:
            _string_bounds(schemas[name], BoundedModel.model_config["str_max_length"])
            schemas[name]["x-nested-json-policy"] = {
                **model.JSON_POLICY,
                "appliesTo": "Each untyped dictionary/array field; typed nested models validate their own fields",
            }
    schema["info"]["description"] = (
        "Requests are limited to 1 MiB before parsing or signature verification. Shared rate and tenant quotas "
        "return 429 with Retry-After. Collection reads use bounded limit/after parameters and optional "
        "X-HCA-Next-Cursor; continue even if visibility filtering leaves an empty page. Nested JSON policies "
        "are published as x-nested-json-policy because depth, total nodes and non-finite numbers are not "
        "fully expressible in JSON Schema. Control values are checked against the original ask's option IDs."
    )
    schema["info"]["x-max-request-body-bytes"] = MAX_BODY_BYTES
    schemas["CollectionRequest"]["x-control-option-counts"] = {
        name: {"minItems": spec["min_options"], "maxItems": spec["max_options"]}
        for name, spec in controls.CONTROLS.items()
    }
    schemas["CollectionRequest"]["properties"]["expiry_policy"]["description"] = (
        "auto_deny or expire closes an overdue ask before accepting an answer; hold permits later answers. "
        "The default lifetime is 7 days and deadlines may be at most 30 days away, configurable by the operator."
    )
    for field in ("answered_by", "answered_role", "surface"):
        schemas["AnswerSubmission"]["properties"][field]["description"] = (
            "Untrusted descriptive client metadata; audit identity, role and surface come from authenticated server state."
        )
        schemas["AnswerRecord"]["properties"][field]["description"] = (
            "Server-derived audit field: authenticated device/Google owner for a human answer; agent or system for automatic outcomes."
        )
    schemas["AnswerRecord"]["properties"]["passthrough"]["description"] = (
        "For human answers, verified_identity snapshots Google subject/email and device_id; client_metadata holds "
        "untrusted descriptions separately. The output allows two extra levels and nine extra nodes for this "
        "server-derived wrapper; the original client payload remains limited to depth 12 and 10000 nodes."
    )
    schemas["AnswerSubmission"]["properties"]["value"]["description"] = (
        "radio/dropdown/approve_deny: one option ID; checkbox/confirm_rows: distinct option IDs "
        "(confirm_rows requires at least one); chat: nonempty text up to 4000 characters. IDs must belong to the ask."
    )

    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method not in HTTP_METHODS:
                continue
            if path == "/v1/internal/expiry-sweep":
                operation["security"] = [{"SweepBearer": []}]
                operation["parameters"] = [p for p in operation.get("parameters", [])
                                           if not (p.get("in") == "header" and p.get("name", "").lower() == "authorization")]
                operation["responses"]["200"]["content"] = {
                    "application/json": {"schema": {"$ref": "#/components/schemas/SweepResult"}}}
            for requirement in operation.get("security", []):
                if "DeviceSignature" in requirement:
                    requirement.update({name: [] for name in DEVICE_HEADERS})
                    for name, (header, maximum, description) in DEVICE_HEADERS.items():
                        operation.setdefault("parameters", []).append({
                            "in": "header", "name": header, "required": True, "description": description,
                            "schema": {"type": "string", "minLength": 1, "maxLength": maximum},
                        })
            if (path, method) in IDEMPOTENT_ROUTES:
                operation.setdefault("parameters", []).append({
                    "in": "header", "name": "Idempotency-Key", "required": False,
                    "schema": {"type": "string", "minLength": 1, "maxLength": idempotency.MAX_KEY},
                    "description": "Optional caller/device-scoped key retained for the configured TTL (default 24 hours). "
                    "Retries return the committed original status/body. Different input or resource with the same key returns 422.",
                })
                for status, response in operation["responses"].items():
                    if status.startswith("2"):
                        response.setdefault("headers", {})["Idempotent-Replayed"] = {
                            "description": "Present with true only when returning a stored idempotent response",
                            "schema": {"type": "string", "enum": ["true"]},
                        }
            statuses = OPERATION_ERRORS.get((path, method), set()) | {"413"}
            if operation.get("security"):
                statuses = statuses | {"401", "403", "429"}
            for status in sorted(statuses):
                response = operation["responses"].setdefault(status, {"description": ERRORS[status]})
                response["content"] = {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPError"}}}
            if path in {"/health", "/healthz"}:
                operation["responses"]["503"]["content"] = {
                    "application/json": {"schema": {"$ref": "#/components/schemas/HealthDegraded"}}}
            # Both model validation and business validation return 422; do not
            # describe only FastAPI's narrower automatic validation envelope.
            if "422" in operation["responses"]:
                operation["responses"]["422"]["content"] = {
                    "application/json": {"schema": {"$ref": "#/components/schemas/HTTPError"}}}
            # HCA-P21b: agent key responses carry the key's per minute budget;
            # 429 adds Retry-After (seconds to the window end).
            if any("AgentKey" in requirement for requirement in operation.get("security", [])):
                for status, response in operation["responses"].items():
                    headers = response.setdefault("headers", {})
                    headers["X-RateLimit-Limit"] = {
                        "description": "The key's rate_per_minute ceiling (or the limit that was hit on 429)",
                        "schema": {"type": "integer", "minimum": 1}}
                    headers["X-RateLimit-Remaining"] = {
                        "description": "Calls left in the current minute window for this key; 0 on 429",
                        "schema": {"type": "integer", "minimum": 0}}
                    if status == "429":
                        headers["Retry-After"] = {
                            "description": "Seconds until the exceeded window ends (minute, or the UTC day for asks_per_day)",
                            "schema": {"type": "integer", "minimum": 1}}
            for response in operation["responses"].values():
                response.setdefault("headers", {})["X-Request-ID"] = {
                    "description": "Server-generated correlation identifier; excludes request content",
                    "schema": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                }
    return schema

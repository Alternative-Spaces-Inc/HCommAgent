"""Bound request memory and produce redacted correlation logs."""
import json
import logging
import time
import urllib.parse
import uuid

log = logging.getLogger("hca.http")
MAX_BODY_BYTES = 1024 * 1024

FORBIDDEN_QUERY_PARAMS = {
    "access_token",
    "api_key",
    "apikey",
    "token",
    "key",
    "secret",
}


class QueryCredentialGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        query_bytes = scope.get("query_string", b"")
        if query_bytes:
            query_str = query_bytes.decode("latin-1")
            params = urllib.parse.parse_qsl(query_str, keep_blank_values=True)
            for name, _ in params:
                if name.lower() in FORBIDDEN_QUERY_PARAMS:
                    await send({
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": json.dumps({"detail": "credentials are not accepted in the query string"}).encode("utf-8"),
                    })
                    return None
        return await self.app(scope, receive, send)


class RateLimitHeaders:
    """HCA-P21b: copies request.state.rate_limit (ceiling, remaining), set by
    require_agent_key, onto the response as X-RateLimit-Limit and
    X-RateLimit-Remaining. Pure ASGI, touches only the response start."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                limit = state.get("rate_limit")
                if limit is not None:
                    headers = list(message.get("headers", []))
                    names = {name.lower() for name, _ in headers}
                    if b"x-ratelimit-limit" not in names:
                        headers.append((b"x-ratelimit-limit", str(limit[0]).encode()))
                        headers.append((b"x-ratelimit-remaining", str(max(0, limit[1])).encode()))
                    message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, send_with_headers)


class RequestBounds:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        size = 0
        buffered = bytearray()
        # Buffer only the bounded input, so oversized chunked uploads fail
        # before parsing, signature verification, or any business mutation.
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > MAX_BODY_BYTES:
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"application/json"), (b"x-request-id", request_id.encode())]})
                await send({"type": "http.response.body", "body": b'{"detail":"request body exceeds 1 MiB"}'})
                return
            buffered.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = bytes(buffered)
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        async def correlated_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [(b"x-request-id", request_id.encode())]
                log.info(json.dumps({"request_id": request_id, "method": scope["method"],
                                     "status": message["status"], "elapsed_ms": round((time.monotonic() - started) * 1000)}))
            await send(message)
        return await self.app(scope, bounded_receive, correlated_send)

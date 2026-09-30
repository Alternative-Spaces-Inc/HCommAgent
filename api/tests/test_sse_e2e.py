"""A live SSE end-to-end test: a real uvicorn subprocess, a real socket, and
httpx's async client reading the streamed body incrementally, instead of
Starlette's synchronous TestClient (which does not stream an open-ended
generator body incrementally and was observed to hang indefinitely trying
to open a connection to GET /v1/inbox/stream; see the note atop
tests/test_inbox_api.py and the PR 13 discussion this closes).

Every await in here sits under one hard wall-clock timeout
(asyncio.wait_for / pytest-timeout as a second, process-level backstop) so
a genuine hang fails the test in seconds instead of hanging CI.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

HARD_TIMEOUT_SECONDS = 20


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(tmp_path):
    """Starts uvicorn app.main:app as a real subprocess on a free port,
    against its own throwaway sqlite database (migrated with alembic
    upgrade head, the same provisioning step a real deploy uses), and
    waits for /healthz to answer before handing control to the test.
    Always terminated at the end, even on failure."""
    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = tmp_path / "hca_sse_e2e.db"
    port = _free_port()
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env.setdefault("HCA_ADMIN_TOKEN", "test-admin-token")
    env.setdefault("WEBHOOK_RETRY_DELAYS", "0,0")

    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=api_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if migrate.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{migrate.stdout}\n{migrate.stderr}")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=api_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"

    try:
        deadline = time.monotonic() + 10
        last_error = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"uvicorn exited early (code {proc.returncode}):\n{out}")
            try:
                resp = httpx.get(f"{base_url}/healthz", timeout=1.0)
                if resp.status_code == 200:
                    break
            except httpx.HTTPError as e:
                last_error = e
            time.sleep(0.2)
        else:
            raise RuntimeError(f"uvicorn never became healthy: {last_error}")

        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


class SSEReader:
    """Wraps a single httpx streaming response's aiter_text() (a one-shot
    async iterator: it cannot be restarted) so multiple "wait for the next
    matching event" calls can share it, each pulling more chunks only as
    needed and buffering across block boundaries."""

    def __init__(self, response_stream):
        self._chunks = response_stream.aiter_text()
        self._buffer = ""

    async def _next_block(self, timeout_seconds):
        while "\n\n" not in self._buffer:
            chunk = await asyncio.wait_for(self._chunks.__anext__(), timeout=timeout_seconds)
            self._buffer += chunk
        block, self._buffer = self._buffer.split("\n\n", 1)
        return block

    async def wait_for(self, predicate, timeout_seconds):
        """predicate(name, data) where name is "__connected__" or
        "__keep_alive__" for a bare comment (data is None), or the SSE
        event name with data parsed from JSON."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no matching event within {timeout_seconds}s")
            block = await self._next_block(remaining)
            if block.startswith(":"):
                name = "__connected__" if block.strip() == ": connected" else "__keep_alive__"
                if predicate(name, None):
                    return name, None
                continue
            event_name = None
            data_dict = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_dict = json.loads(line[len("data:"):].strip())
            if event_name is not None and predicate(event_name, data_dict):
                return event_name, data_dict


async def _sse_e2e(base_url):
    request_body = {
        "caller": "agent-sse-e2e",
        "audience": "owner",
        "title": "pick a lane, live over SSE",
        "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "suggested": {"option_id": "a", "why": "A is simpler"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }

    async with httpx.AsyncClient(base_url=base_url, timeout=HARD_TIMEOUT_SECONDS) as client:
        async with client.stream("GET", "/v1/inbox/stream") as stream_resp:
            assert stream_resp.status_code == 200
            assert stream_resp.headers["content-type"].startswith("text/event-stream")

            reader = SSEReader(stream_resp)

            # Wait for the initial connected comment before creating the ask,
            # so the create below cannot race ahead of subscriber registration
            # (events.py's publish() only reaches queues already subscribed
            # when it is called).
            await reader.wait_for(lambda name, data: name == "__connected__", HARD_TIMEOUT_SECONDS)

            # Mint the caller's key through the live admin endpoint (HCA_ADMIN_TOKEN
            # is inherited by the uvicorn subprocess from the test env), then
            # create the ask as that caller.
            admin_token = os.environ.get("HCA_ADMIN_TOKEN", "test-admin-token")
            key_resp = await client.post(
                "/v1/api-keys", json={"caller": request_body["caller"]},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert key_resp.status_code == 201, key_resp.text
            agent_headers = {"Authorization": f"Bearer {key_resp.json()['api_key']}"}

            create_resp = await client.post("/v1/requests", json=request_body, headers=agent_headers)
            assert create_resp.status_code == 201
            request_id = create_resp.json()["request_id"]

            created_name, created_envelope = await reader.wait_for(
                lambda name, data: name == "request.created" and data and data.get("data", {}).get("request_id") == request_id,
                HARD_TIMEOUT_SECONDS,
            )
            assert created_name == "request.created"
            assert created_envelope["data"]["request_id"] == request_id
            assert created_envelope["data"]["title"] == request_body["title"]

            answer_resp = await client.post(
                f"/v1/requests/{request_id}/answer",
                json={"control": "radio", "value": "a", "surface": "browser_overlay"},
            )
            assert answer_resp.status_code == 201

            answered_name, answered_envelope = await reader.wait_for(
                lambda name, data: name == "request.answered" and data and data.get("data", {}).get("request_id") == request_id,
                HARD_TIMEOUT_SECONDS,
            )
            assert answered_name == "request.answered"
            assert answered_envelope["data"]["request_id"] == request_id
            assert answered_envelope["data"]["value"] == "a"
            assert answered_envelope["data"]["surface"] == "browser_overlay"


@pytest.mark.timeout(HARD_TIMEOUT_SECONDS + 10)
def test_sse_stream_delivers_created_then_answered_over_a_real_socket(live_server):
    asyncio.run(asyncio.wait_for(_sse_e2e(live_server), timeout=HARD_TIMEOUT_SECONDS))

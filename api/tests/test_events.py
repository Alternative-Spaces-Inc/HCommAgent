import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from app.events import subscribe, unsubscribe, publish, format_sse


def test_publish_reaches_subscriber_queue():
    async def run():
        q = subscribe()
        try:
            publish("request.created", {"request_id": "abc123"})
            envelope = await asyncio.wait_for(q.get(), timeout=1)
            assert envelope["event"] == "request.created"
            assert envelope["data"]["request_id"] == "abc123"
            sse_text = format_sse(envelope)
            assert sse_text.startswith("event: request.created\n")
            assert "abc123" in sse_text
        finally:
            unsubscribe(q)

    asyncio.run(run())


def test_publish_rejects_unknown_event():
    try:
        publish("request.made_up", {})
        assert False, "expected ValueError"
    except ValueError:
        pass

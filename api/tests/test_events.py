import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from app.events import subscribe, unsubscribe, publish, format_sse, dropped, QUEUE_MAXSIZE


def test_publish_reaches_subscriber_queue():
    async def run():
        q = subscribe("dev-a", "tenant-a")
        try:
            publish("request.created", {"request_id": "abc123"}, tenant_id="tenant-a", device_ids=["dev-a"])
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
        publish("request.made_up", {}, tenant_id="tenant-a")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_publish_filters_by_tenant_and_by_device_before_enqueue():
    """HCA-P19: authorization happens on the server, before the envelope
    reaches a queue; visible_to in the payload is not what keeps data in."""
    async def run():
        a1 = subscribe("dev-a1", "tenant-a")
        a2 = subscribe("dev-a2", "tenant-a")
        b1 = subscribe("dev-b1", "tenant-b")
        try:
            publish("request.created", {"request_id": "r1", "title": "secret for a1"}, tenant_id="tenant-a", device_ids=["dev-a1"])
            assert (await asyncio.wait_for(a1.get(), timeout=1))["data"]["request_id"] == "r1"
            assert a2.empty(), "a same-tenant device outside the routing set got the ask"
            assert b1.empty(), "another tenant's device got the ask"
            # terminal events go to the whole tenant, still never across it
            publish("request.answered", {"request_id": "r1"}, tenant_id="tenant-a")
            assert (await asyncio.wait_for(a1.get(), timeout=1))["event"] == "request.answered"
            assert (await asyncio.wait_for(a2.get(), timeout=1))["event"] == "request.answered"
            assert b1.empty()
        finally:
            for q in (a1, a2, b1):
                unsubscribe(q)

    asyncio.run(run())


def test_subscribe_requires_identity_and_publish_requires_tenant():
    for args in (("", "t"), ("d", "")):
        try:
            subscribe(*args)
            assert False, "expected ValueError"
        except ValueError:
            pass
    try:
        publish("request.created", {}, tenant_id="")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_queue_overflow_is_counted_not_silent():
    async def run():
        q = subscribe("dev-full", "tenant-a")
        try:
            before = dropped.get("dev-full", 0)
            for _ in range(QUEUE_MAXSIZE + 3):
                publish("request.answered", {"request_id": "x"}, tenant_id="tenant-a")
            assert dropped["dev-full"] == before + 3
        finally:
            unsubscribe(q)

    asyncio.run(run())

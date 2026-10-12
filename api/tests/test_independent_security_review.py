"""Independent regressions for reviewed boundaries; synthetic data and isolated test DB."""
import asyncio
from types import SimpleNamespace

from app import inbox_api, models




def test_replay_checks_revocation_between_delayed_deliveries(monkeypatch):
    replay = [{"id": n, "event": "request.answered", "data": {"request_id": f"synthetic-{n}"}}
              for n in (1, 2)]
    monkeypatch.setattr(inbox_api, "_replay_for", lambda *args: replay)

    async def scenario():
        allowed = [True]
        checks = []
        def authorized(_):
            checks.append(allowed[0])
            return allowed[0]
        source = inbox_api.inbox_event_source(models.Device(id="review-replay", tenant_id="default"),
            keepalive_seconds=.02, authorized_fn=authorized, last_event_id=0)
        try:
            assert await anext(source) == ": connected\n\n"
            assert "synthetic-1" in await anext(source)
            allowed[0] = False
            await asyncio.sleep(.04)
            chunk = await anext(source)
            assert "stream.closed" in chunk and "synthetic-2" not in chunk
            assert len(checks) >= 2
        finally:
            await source.aclose()
    asyncio.run(scenario())


def test_event_before_authorization_deadline_does_not_restart_clock(monkeypatch):
    """A last-second event followed by silence keeps the original deadline."""
    async def scenario():
        clock = [0.0]
        allowed = [True]
        checks = []
        waits = []
        q = asyncio.Queue()
        q.put_nowait({"id": 1, "event": "request.answered", "data": {"request_id": "before-deadline"}})
        monkeypatch.setattr(inbox_api, "subscribe", lambda *args: q)
        monkeypatch.setattr(inbox_api, "unsubscribe", lambda *args: None)
        monkeypatch.setattr(inbox_api, "is_closed", lambda *args: False)
        monkeypatch.setattr(inbox_api, "time", SimpleNamespace(monotonic=lambda: clock[0]))

        async def controlled_wait(waiter, timeout):
            waits.append(timeout)
            if len(waits) == 1:
                clock[0] = .99
                return await waiter
            waiter.close()
            clock[0] += timeout
            raise asyncio.TimeoutError

        monkeypatch.setattr(inbox_api, "asyncio", SimpleNamespace(
            to_thread=asyncio.to_thread, wait_for=controlled_wait, TimeoutError=asyncio.TimeoutError))

        def authorized(_):
            checks.append(clock[0])
            return allowed[0]

        source = inbox_api.inbox_event_source(models.Device(id="review-quiet", tenant_id="default"),
            keepalive_seconds=1, authorized_fn=authorized)
        try:
            assert await anext(source) == ": connected\n\n"
            assert "before-deadline" in await anext(source)
            allowed[0] = False
            assert "stream.closed" in await anext(source)
            assert .009 < waits[1] < .011
            assert checks == [0.0, 1.0]
        finally:
            await source.aclose()
    asyncio.run(scenario())


def test_event_arriving_at_authorization_deadline_is_checked_before_delivery(monkeypatch):
    async def scenario():
        clock = [0.0]
        allowed = [True]
        q = asyncio.Queue()
        q.put_nowait({"id": 1, "event": "request.answered", "data": {"request_id": "sensitive-late-event"}})
        monkeypatch.setattr(inbox_api, "subscribe", lambda *args: q)
        monkeypatch.setattr(inbox_api, "unsubscribe", lambda *args: None)
        monkeypatch.setattr(inbox_api, "time", SimpleNamespace(monotonic=lambda: clock[0]))

        async def controlled_wait(waiter, timeout):
            envelope = await waiter
            allowed[0] = False
            clock[0] = 1.001
            return envelope

        monkeypatch.setattr(inbox_api, "asyncio", SimpleNamespace(
            to_thread=asyncio.to_thread, wait_for=controlled_wait, TimeoutError=asyncio.TimeoutError))
        source = inbox_api.inbox_event_source(models.Device(id="review-late", tenant_id="default"),
            keepalive_seconds=1, authorized_fn=lambda _: allowed[0])
        try:
            assert await anext(source) == ": connected\n\n"
            chunk = await anext(source)
            assert "stream.closed" in chunk and "sensitive-late-event" not in chunk
        finally:
            await source.aclose()
    asyncio.run(scenario())

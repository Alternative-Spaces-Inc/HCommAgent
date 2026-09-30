"""Shared test wiring.

Every in-process test posts asks whose deliver_to points at a made-up
webhook URL. Since HCA-P4 the service actually POSTs outcomes there, so
without this fixture each answer, cancel or expiry would try a real DNS
lookup, fail, and sleep through the retry delays. A MockTransport is
installed for every test by default (recording every outbound request
and answering 200); tests that need failures swap in their own via
webhooks.set_transport_factory and the fixture restores the default
afterwards. Retry delays are zeroed for the whole suite.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("WEBHOOK_RETRY_DELAYS", "0,0")

import httpx
import pytest

from app import webhooks


class WebhookRecorder:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status_code = 200

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status_code, json={"received": True})
        return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def webhook_recorder():
    rec = WebhookRecorder()
    webhooks.set_transport_factory(rec.transport)
    try:
        yield rec
    finally:
        webhooks.set_transport_factory(None)

"""Shared test wiring.

Every in-process test posts asks whose deliver_to points at a made-up
webhook URL. Since HCA-P4 the service actually POSTs outcomes there, so
without this fixture each answer, cancel or expiry would try a real DNS
lookup, fail, and sleep through the retry delays. A MockTransport is
installed for every test by default (recording every outbound request
and answering 200); tests that need failures swap in their own via
webhooks.set_transport_factory and the fixture restores the default
afterwards. Retry delays are zeroed for the whole suite.

HCA_ADMIN_TOKEN is fixed for the suite so POST /v1/api-keys (admin
bearer since HCA-P4) can mint agent keys; tests/authhelper.py builds the
agent and admin headers.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Validate before importing app.db: legacy module collection used to call
# drop_all against any inherited DATABASE_URL, including a live database.
# Default runs get an isolated disposable database outside the repository.
import pytest
from sqlalchemy.engine import make_url


_DISPOSABLE_DIRECTORIES = []


def _test_database_url():
    raw = os.environ.get("HCA_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        directory = Path(tempfile.mkdtemp(prefix="hca-pytest-"))
        _DISPOSABLE_DIRECTORIES.append(directory)
        return "sqlite:///" + str(directory / "hca_test.db")
    try:
        url = make_url(raw)
    except Exception:
        raise pytest.UsageError("Test DATABASE_URL is invalid (credentials omitted)") from None
    if url.get_backend_name() == "postgresql":
        name = url.database or ""
        if url.host not in {"localhost", "127.0.0.1", "::1", "postgres"} or not (
                name.startswith("test_") or name.endswith("_test")):
            raise pytest.UsageError("Tests require a local PostgreSQL database named test_* or *_test; production databases are refused")
    elif url.get_backend_name() == "sqlite":
        if url.database != ":memory:":
            path = Path(url.database or "").resolve()
            in_temp = any(path.is_relative_to(Path(root).resolve()) for root in {tempfile.gettempdir(), "/tmp", "/private/tmp"})
            if not in_temp and "test" not in path.name.lower():
                raise pytest.UsageError("Tests require a temporary SQLite path or a file with 'test' in its name; application databases are refused")
    else:
        raise pytest.UsageError("Tests support disposable SQLite or local PostgreSQL test databases only")
    return raw


os.environ["DATABASE_URL"] = _test_database_url()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("WEBHOOK_RETRY_DELAYS", "0,0")
os.environ.setdefault("HCA_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("HCA_API_KEY_PEPPER", "test-pepper-0123456789abcdef")

import httpx  # noqa: E402 -- database safety checks must precede app imports

from app import ssrf, webhooks  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def disposable_database_cleanup():
    yield
    for directory in _DISPOSABLE_DIRECTORIES:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(scope="module", autouse=True)
def isolated_module_database():
    """Rebuild once per module; preserve module fixtures and stop workers.

    Modules share an engine but do not inherit another module's asks, jobs,
    owner removals or counters. No collection/import performs destructive
    DDL. Multi-process servers are stopped by their fixture before teardown.
    """
    from app import events, jobs, devicesig, models
    from app.db import Base, engine, SessionLocal
    jobs.stop_worker()
    events.stop_listener()
    events.wake_all()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        models.ensure_default_tenant(db)
    devicesig.replay_guard.reset()
    events.dropped.clear()
    for key in events.replay_state:
        events.replay_state[key] = 0
    for key in events.stream_state:
        events.stream_state[key] = 0
    for key in ("polls", "claimed", "done", "failed", "reconciled"):
        jobs.worker_state[key] = 0
    jobs.worker_state["last_error"] = None
    yield
    jobs.stop_worker()
    events.stop_listener()
    events.wake_all()
    engine.dispose()


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


@pytest.fixture(autouse=True)
def default_tenant():
    """HCA-P19: test modules rebuild the schema from metadata, which has no
    default tenant row; every api key, device and ask needs one."""
    from app.db import SessionLocal
    from app import models
    with SessionLocal() as db:
        models.ensure_default_tenant(db)
    yield


PUBLIC_TEST_ADDRESS = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_resolver():
    """HCA-P21a: destination URLs pass the SSRF rules without DNS. Every
    host resolves to one public address (the documentation ranges count as
    non global for the ipaddress module, so a real one is used); tests of
    the rules install their own resolver on top."""
    ssrf.set_resolver(lambda host: [PUBLIC_TEST_ADDRESS])
    try:
        yield
    finally:
        ssrf.set_resolver(None)

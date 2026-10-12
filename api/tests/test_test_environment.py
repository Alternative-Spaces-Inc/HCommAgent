"""Prevent a developer's inherited production credentials from destructive test DDL."""
import pytest

from conftest import _test_database_url


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2://user:secret@db.example.com:5432/hca_test",
    "postgresql+psycopg2://user:secret@localhost:5432/hca",
    "sqlite:///./hca_local.db",
    "mysql://user:secret@localhost/test_hca",
    "not-a-database-url",
])
def test_refuses_unsafe_database_before_app_imports(monkeypatch, url):
    monkeypatch.delenv("HCA_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(pytest.UsageError) as error:
        _test_database_url()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2://user:secret@localhost:5432/hca_test",
    "postgresql://user:secret@127.0.0.1/test_hca",
    "sqlite:////private/tmp/hca-any-test.db",
])
def test_accepts_explicit_disposable_database(monkeypatch, url):
    monkeypatch.delenv("HCA_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", url)
    assert _test_database_url() == url


def test_default_is_a_new_temporary_database_each_run(monkeypatch):
    monkeypatch.delenv("HCA_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    first, second = _test_database_url(), _test_database_url()
    assert first != second
    assert first.startswith("sqlite:///") and first.endswith("hca_test.db")

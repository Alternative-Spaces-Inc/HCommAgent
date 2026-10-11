from app.db import pool_kwargs


def test_sqlite_gets_no_pool_arguments():
    assert pool_kwargs("sqlite:///./x.db", {}) == {}


def test_postgres_defaults_are_small():
    k = pool_kwargs("postgresql+psycopg2://u@/db", {})
    assert k == {"pool_size": 3, "max_overflow": 3, "pool_timeout": 10, "pool_recycle": 300}


def test_postgres_bounds_come_from_environment():
    env = {"HCA_DB_POOL_SIZE": "8", "HCA_DB_MAX_OVERFLOW": "4", "HCA_DB_POOL_TIMEOUT": "20", "HCA_DB_POOL_RECYCLE": "900"}
    k = pool_kwargs("postgresql+psycopg2://u@/db", env)
    assert k == {"pool_size": 8, "max_overflow": 4, "pool_timeout": 20, "pool_recycle": 900}

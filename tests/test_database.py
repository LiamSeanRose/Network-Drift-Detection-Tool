"""tests/test_database.py — engine / sessionmaker singleton behaviour.

`get_engine()` builds a SQLAlchemy connection pool. The scheduler calls
`get_sessionmaker()` once per device per poll cycle (via
`pipeline.run_drift_check`), so a non-memoized `get_engine()` spins up a brand
new pool every time and never reuses it — a slow connection leak. These tests
pin the contract: the default engine and sessionmaker are process-wide
singletons, while an explicitly-supplied engine is never memoized (tests rely
on that to bind a sessionmaker to their own throwaway in-memory database).

No Postgres needed: DATABASE_URL is pointed at sqlite for the duration of each
test via monkeypatch.
"""

from sqlalchemy.engine import Engine

from netdrift.storage import database


def _reset(monkeypatch):
    """Point DATABASE_URL at sqlite and clear any cached singleton.

    `_reset()` is part of the singleton deliverable; guard it with getattr so
    that BEFORE the fix exists this helper is a no-op and the identity
    assertions below are what fail (the meaningful failure), not an
    AttributeError on a missing function.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    getattr(database, "_reset", lambda: None)()


def test_get_engine_returns_same_instance(monkeypatch):
    _reset(monkeypatch)
    first = database.get_engine()
    second = database.get_engine()
    assert isinstance(first, Engine)
    assert first is second  # memoized: one pool per process


def test_get_sessionmaker_returns_same_instance(monkeypatch):
    _reset(monkeypatch)
    first = database.get_sessionmaker()
    second = database.get_sessionmaker()
    assert first is second


def test_default_sessionmaker_is_bound_to_the_singleton_engine(monkeypatch):
    _reset(monkeypatch)
    assert database.get_sessionmaker().kw["bind"] is database.get_engine()


def test_explicit_engine_is_not_memoized(monkeypatch):
    """Passing an engine bypasses the singleton entirely — each call yields a
    fresh sessionmaker bound to exactly that engine. Tests depend on this to
    bind to their own in-memory database without polluting the process cache."""
    _reset(monkeypatch)
    throwaway = database.create_engine("sqlite://")
    sm_a = database.get_sessionmaker(engine=throwaway)
    sm_b = database.get_sessionmaker(engine=throwaway)
    assert sm_a is not sm_b
    assert sm_a.kw["bind"] is throwaway
    # And the explicit call must not become the cached default.
    assert database.get_sessionmaker() is not sm_a


def test_reset_clears_the_singletons(monkeypatch):
    _reset(monkeypatch)
    engine_before = database.get_engine()
    database._reset()
    assert database.get_engine() is not engine_before

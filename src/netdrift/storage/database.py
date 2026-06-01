"""storage/database.py — database connection setup (v0.2).

Owns the SQLAlchemy "engine" (the live connection to Postgres) and the
"session" factory (how individual units of work talk to the database).

Connection details come from a single environment variable, DATABASE_URL,
mirroring how netbox_client.py reads NETBOX_URL / NETBOX_TOKEN. Keeping all
configuration in environment variables means the app has exactly one way to
be configured, and no secret is ever written to a file. Example value:

    postgresql+psycopg://postgres:devpassword@localhost:5432/netdrift

That string encodes: dialect+driver :// user : password @ host : port / dbname
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netdrift.storage.models import Base


def _database_url():
    """Read DATABASE_URL from the environment, or fail loudly.

    Loud failure (matching netbox_client._connect) is deliberate: a missing
    connection string should stop the program with a clear message, not let
    it limp on and fail later with something cryptic.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable must be set, e.g.\n"
            "  postgresql+psycopg://postgres:devpassword@localhost:5432/netdrift"
        )
    return url


# Process-wide singletons. A SQLAlchemy Engine owns a connection pool, so we
# want exactly one per process — not one per call. Before this was memoized,
# the scheduler built a fresh pool on every poll cycle (pipeline.run_drift_check
# calls get_sessionmaker() per device) and leaked connections steadily.
_engine = None
_sessionmaker = None


def get_engine():
    """Return the process-wide SQLAlchemy engine, building it on first use.

    The engine manages the actual connection pool to Postgres. One engine per
    process is the norm, so it is memoized: the first call constructs the pool,
    every later call returns the same engine object.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(_database_url())
    return _engine


def get_sessionmaker(engine=None):
    """Return a Session factory. A Session is one conversation with the
    database: you open one, do some work, commit, close.

    With no argument this returns the process-wide singleton factory bound to
    the singleton engine. Passing an explicit ``engine`` bypasses the singleton
    entirely and yields a fresh factory bound to that engine — tests rely on
    this to bind to their own throwaway in-memory database without polluting
    (or being polluted by) the process-wide cache.
    """
    if engine is not None:
        return sessionmaker(bind=engine)
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(bind=get_engine())
    return _sessionmaker


def _reset():
    """Clear the cached engine and sessionmaker. TESTS ONLY — mirrors the
    registry modules' _reset() helpers so a test can force a rebuild (e.g. after
    pointing DATABASE_URL somewhere new). Not for application use."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


def create_all(engine=None):
    """Create every table defined on Base. TESTS ONLY — not for app use.

    Alembic migrations are now the source of truth for the real database
    schema (run `alembic upgrade head`). This helper remains only because the
    test suite uses an in-memory SQLite database where a one-shot create is
    simpler than running migrations. The application must NOT call this against
    Postgres — doing so would create tables outside Alembic's tracking and let
    the real schema drift from the migration history.
    """
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
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


def get_engine():
    """Create the SQLAlchemy engine — the object that manages the actual
    connection pool to Postgres. One engine per process is the norm."""
    return create_engine(_database_url())


def get_sessionmaker(engine=None):
    """Return a Session factory bound to the engine. A Session is one
    conversation with the database: you open one, do some work, commit, close."""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine)


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
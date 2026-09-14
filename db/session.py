"""
db/session.py
----------------
Engine/session factory. Reads DATABASE_URL from the environment -- never
hardcodes credentials (blueprint section 42: "Secrets in environment
variables... Never expose... Database passwords... to the frontend").

Expected format:
    postgresql+psycopg2://user:password@host:port/dbname

No default value is provided for production use; only the test suite
supplies its own fallback (a local database created for this session --
see tests/test_db_schema.py), so a missing DATABASE_URL fails loudly in
real usage instead of silently pointing somewhere unexpected.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def get_engine(database_url: str = None):
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: "
            "postgresql+psycopg2://farmlink:<password>@localhost:5432/farmlink"
        )
    return create_engine(
        url,
        future=True,
        # Found during COMPONENT #3's testing: after a Postgres restart
        # (or any dropped connection -- firewall timeout, DB failover,
        # etc. in real deployment, not just a sandbox artifact), a
        # pooled connection created before the restart is stale and
        # fails on first use with "server closed the connection
        # unexpectedly" -- exactly one request/test hits this, then the
        # pool discards it and everything after succeeds. pool_pre_ping
        # makes SQLAlchemy issue a cheap "SELECT 1" before handing out a
        # pooled connection and transparently reconnect if it's dead,
        # instead of surfacing that failure to the caller. Standard
        # practice for any long-lived connection pool, not a workaround
        # specific to this project's test environment.
        pool_pre_ping=True,
    )


def get_session_factory(database_url: str = None):
    engine = get_engine(database_url)
    return sessionmaker(bind=engine, future=True)

"""
tests/test_decision_pipeline_postgres_mode.py
-------------------------------------------------
Cross-checks that data_source_mode="postgres" produces results
consistent with data_source_mode="direct" for the same query, against a
real, disposable Postgres database (same fixture convention as
test_repositories.py). This is the "postgres" analog of
test_decision_pipeline_adapter_mode.py -- proves the DB-backed path is
a genuine alternate route to the same numbers, not parallel dead code.
"""

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from tools.decision_pipeline import build_sell_decision

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+psycopg2://farmlink:farmlink@localhost:5432/postgres",
)
TEST_DB_NAME = "farmlink_pipeline_pg_pytest"
TEST_DB_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


def _run_alembic(*args):
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + list(args),
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    return result


@pytest.fixture(scope="module")
def seeded_db():
    admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))

    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, f"migration failed: {result.stderr}"

    from db import seed_from_json
    seed_from_json.run(database_url=TEST_DB_URL)

    yield TEST_DB_URL

    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture
def db_session(seeded_db):
    engine = create_engine(seeded_db, future=True, poolclass=NullPool)
    Session = sessionmaker(bind=engine, future=True)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


def test_postgres_mode_requires_session():
    with pytest.raises(ValueError):
        build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="postgres")


def test_invalid_mode_still_rejected():
    with pytest.raises(ValueError):
        build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="not_a_mode")


def test_direct_and_postgres_produce_same_price(db_session):
    direct = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="direct")
    postgres = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="postgres", db_session=db_session)
    assert direct["price_forecast"]["current_modal"] == postgres["price_forecast"]["current_modal"]
    assert direct["price_forecast"]["trend"] == postgres["price_forecast"]["trend"]
    assert postgres["price_forecast"]["source"] == "POSTGRES"


def test_direct_and_postgres_produce_same_recommendation(db_session):
    direct = build_sell_decision("Cotton", "Nagpur", 30, "B", data_source_mode="direct")
    postgres = build_sell_decision("Cotton", "Nagpur", 30, "B", data_source_mode="postgres", db_session=db_session)
    assert direct["recommended_action"] == postgres["recommended_action"]


def test_direct_and_postgres_produce_same_top_buyer_and_net_realization(db_session):
    direct = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="direct")
    postgres = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="postgres", db_session=db_session)
    assert direct["buyer_shortlist"][0]["buyer_name"] == postgres["buyer_shortlist"][0]["buyer_name"]
    assert direct["buyer_shortlist"][0]["net_realization"] == postgres["buyer_shortlist"][0]["net_realization"]
    assert direct["buyer_shortlist"][0]["match_score"] == postgres["buyer_shortlist"][0]["match_score"]


def test_postgres_mode_data_status_summary_labeled_correctly(db_session):
    result = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="postgres", db_session=db_session)
    assert "POSTGRES" in result["data_status_summary"]["buyer_data"]

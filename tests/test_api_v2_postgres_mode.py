"""
tests/test_api_v2_postgres_mode.py
-------------------------------------
API-level tests for data_source_mode="postgres" on /agents/sell-decision,
against a real database (same seeded-DB fixture convention as
test_decision_pipeline_postgres_mode.py). Separate file from
test_api_v2.py because these need a live Postgres and DATABASE_URL set;
test_api_v2.py's existing 11 tests deliberately need neither.
"""

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+psycopg2://farmlink:farmlink@localhost:5432/postgres",
)
TEST_DB_NAME = "farmlink_api_pg_pytest"
TEST_DB_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


def _run_alembic():
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"migration failed: {result.stderr}"


@pytest.fixture(scope="module")
def seeded_db_url():
    admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    _run_alembic()
    from db import seed_from_json
    seed_from_json.run(database_url=TEST_DB_URL)

    yield TEST_DB_URL

    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture
def client(seeded_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", seeded_db_url)
    from fastapi.testclient import TestClient
    import importlib
    import api_v2
    importlib.reload(api_v2)  # ensure DATABASE_URL env var is picked up fresh
    return TestClient(api_v2.app)


def test_postgres_mode_via_api_matches_direct_mode(client):
    direct = client.post("/agents/sell-decision", json={
        "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
        "include_llm_explanation": False,
    })
    postgres = client.post("/agents/sell-decision", json={
        "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
        "include_llm_explanation": False, "data_source_mode": "postgres",
    })
    assert direct.status_code == 200
    assert postgres.status_code == 200
    assert direct.json()["recommended_action"] == postgres.json()["recommended_action"]
    assert postgres.json()["price_forecast"]["source"] == "POSTGRES"
    assert (direct.json()["buyer_shortlist"][0]["buyer_name"]
            == postgres.json()["buyer_shortlist"][0]["buyer_name"])


def test_postgres_mode_db_down_returns_clean_503_not_raw_500(client, monkeypatch):
    # Point DATABASE_URL at a port nothing is listening on, reload so the
    # app picks it up, and confirm the failure is a structured 503 --
    # not an unhandled exception/raw traceback reaching the client.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://farmlink:farmlink@localhost:59999/nope")
    import importlib
    import api_v2
    importlib.reload(api_v2)
    from fastapi.testclient import TestClient
    broken_client = TestClient(api_v2.app)

    r = broken_client.post("/agents/sell-decision", json={
        "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
        "include_llm_explanation": False, "data_source_mode": "postgres",
    })
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "postgres_unavailable"


def test_invalid_data_source_mode_rejected_with_422():
    # Deliberately NOT using the `client` fixture (which requires a live,
    # seeded Postgres via seeded_db_url) -- the 422 check in api_v2.py's
    # sell_decision() runs BEFORE _open_db_session() is ever called, so
    # this test needs no database at all. Depending on the DB-seeding
    # fixture here caused an intermittent "ERROR at setup" failure
    # (occasional connection races unrelated to this test's actual
    # purpose) purely because it shared a file/fixture with two tests
    # that DO need a live DB. Removing that unneeded dependency is the
    # real fix -- a test shouldn't require infrastructure it doesn't use.
    from fastapi.testclient import TestClient
    import api_v2
    plain_client = TestClient(api_v2.app)
    r = plain_client.post("/agents/sell-decision", json={
        "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
        "data_source_mode": "made_up_mode",
    })
    assert r.status_code == 422

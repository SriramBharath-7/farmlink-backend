"""
tests/test_repositories.py
-----------------------------
Integration tests for db/repositories.py, against a real, disposable
Postgres database (same convention as test_db_schema.py -- creates its
own scratch DB, drops it at the end). Verifies the Postgres-backed data
access produces dicts shaped correctly for forecast_tool.py/
matching_engine.py to consume directly, and cross-checks specific values
against the source JSON files rather than just checking row counts.
"""

import os
import subprocess
import sys
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from db.repositories import get_price_records, get_candidate_buyers
from tools.forecast_tool import forecast_price

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+psycopg2://farmlink:farmlink@localhost:5432/postgres",
)
TEST_DB_NAME = "farmlink_repositories_pytest"
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
    """Fresh scratch database, migrated AND seeded once for this whole
    test module -- these tests are read-only against it, so sharing the
    SEEDED DATA is safe. Each test still gets its own engine/session
    (see the `session` fixture below) rather than sharing a connection
    pool across tests, so there's no risk of a pooled-but-idle
    connection blocking this teardown's DROP DATABASE."""
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
def session(seeded_db):
    """Function-scoped: a fresh engine + session per test, fully
    disposed at teardown, so nothing outlives an individual test and
    the module-scoped DROP DATABASE above never has to wait on a
    lingering pooled connection."""
    engine = create_engine(seeded_db, future=True, poolclass=NullPool)
    Session = sessionmaker(bind=engine, future=True)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


def test_get_price_records_shape_matches_forecast_tool_expectations(session):
    records = get_price_records(session, "Onion", "Jalgaon")
    assert len(records) > 0
    r = records[0]
    assert set(["commodity", "district", "arrival_date", "modal_price", "min_price", "max_price"]).issubset(r.keys())
    # arrival_date must be DD/MM/YYYY string, matching forecast_tool.py's
    # _parse_date exactly -- this is the whole point of this repository layer
    import re
    assert re.match(r"^\d{2}/\d{2}/\d{4}$", r["arrival_date"])


def test_get_price_records_feeds_forecast_tool_directly_no_shim(session):
    # Onion/Akola has 4 records in the source data -- enough for
    # forecast_price's MIN_RECORDS_FOR_TREND=3 threshold. (Onion/Jalgaon,
    # used in the shape test above, only has 2 -- correctly triggers
    # insufficient_data there, which is why this test uses a different pair.)
    records = get_price_records(session, "Onion", "Akola")
    forecast = forecast_price(records)
    assert forecast["insufficient_data"] is False
    assert forecast["current_modal"] > 0
    assert forecast["trend"] in ("UPWARD", "DOWNWARD", "STABLE")


def test_get_price_records_district_filter_narrows_results(session):
    all_onion = get_price_records(session, "Onion")
    jalgaon_onion = get_price_records(session, "Onion", "Jalgaon")
    assert len(jalgaon_onion) < len(all_onion)
    assert all(r["district"].lower() == "jalgaon" for r in jalgaon_onion)


def test_get_price_records_unknown_commodity_returns_empty_not_error(session):
    records = get_price_records(session, "Dragonfruit", "Nashik")
    assert records == []


def test_get_price_records_values_match_source_json(session):
    data_dir = os.path.join(BASE_DIR, "data")
    with open(os.path.join(data_dir, "mock_mandi_prices.json")) as f:
        source = json.load(f)
    expected = [r for r in source if r["commodity"] == "Onion" and r["district"] == "Jalgaon"]

    records = get_price_records(session, "Onion", "Jalgaon")
    assert len(records) == len(expected)
    db_modal_prices = sorted(r["modal_price"] for r in records)
    json_modal_prices = sorted(r["modal_price"] for r in expected)
    assert db_modal_prices == json_modal_prices


def test_get_candidate_buyers_shape(session):
    buyers = get_candidate_buyers(session, "Onion")
    assert len(buyers) > 0
    b = buyers[0]
    assert set(["buyer_id", "name", "district", "commodities_wanted",
                "typical_volume_quintals", "reliability_score",
                "target_price_index", "minimum_grade_accepted", "kyc_verified"]).issubset(b.keys())
    assert b["commodities_wanted"] == ["Onion"]


def test_get_candidate_buyers_kyc_filter(session):
    all_buyers = get_candidate_buyers(session, "Turmeric", kyc_verified_only=False)
    verified_only = get_candidate_buyers(session, "Turmeric", kyc_verified_only=True)
    assert len(verified_only) <= len(all_buyers)
    assert all(b["kyc_verified"] for b in verified_only)


def test_get_candidate_buyers_matches_source_json_for_one_buyer(session):
    data_dir = os.path.join(BASE_DIR, "data")
    with open(os.path.join(data_dir, "buyers_augmented.json")) as f:
        source = json.load(f)
    b0001 = next(b for b in source if b["buyer_id"] == "B0001")
    assert "Bajra" in b0001["commodities_wanted"]

    buyers = get_candidate_buyers(session, "Bajra", kyc_verified_only=False)
    match = next(b for b in buyers if b["buyer_id"] == "B0001")
    assert match["name"] == b0001["name"]
    assert match["typical_volume_quintals"] == b0001["typical_volume_quintals"]
    assert match["target_price_index"] == b0001["target_price_index"]


def test_get_candidate_buyers_feeds_matching_engine_directly(session):
    from tools.matching_engine import rank_matched_buyers
    buyers = get_candidate_buyers(session, "Onion")

    def fake_lookup(district):
        return (100.0, 1000.0)

    ranked = rank_matched_buyers(
        candidate_buyers=buyers, commodity="Onion", farmer_grade="A",
        quantity_quintals=20, mandi_modal_price=2700,
        distance_and_cost_lookup=fake_lookup,
    )
    assert len(ranked) == len(buyers)
    scores = [r["match_score"] for r in ranked if r["meets_grade_requirement"]]
    assert scores == sorted(scores, reverse=True)

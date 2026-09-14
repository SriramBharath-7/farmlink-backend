"""
tests/test_db_schema.py
--------------------------
Integration tests for db/models.py + migrations/. These run against a
real, disposable PostgreSQL database -- not SQLite, not mocks -- because
several things being tested (JSONB, array columns, the transaction FSM
trigger) are Postgres-specific and would either silently no-op or fail
to even parse against SQLite.

Requires a reachable Postgres server with a superuser-capable role. Set
TEST_DATABASE_ADMIN_URL to a URL that can CREATE/DROP DATABASE (defaults
to the local role created for this delivery: farmlink/farmlink@localhost).
The test suite creates its own scratch database
(farmlink_schema_pytest) and drops it at the end of the run, so it never
touches farmlink_test (used for manual/demo seeding) or a real deployment
database.

What this suite actually verifies (see DB_REPORT.md for what it does
NOT verify):
  - Both migrations apply cleanly to an empty database.
  - All 20 expected tables exist afterward.
  - Key CHECK constraints reject invalid data (price bounds, positive
    quantity).
  - Uniqueness and foreign-key constraints are enforced.
  - The transaction status trigger enforces the blueprint's state
    machine, including rejecting illegal jumps and enforcing terminal
    states.
  - A full farmer->lot->offer->transaction->payment->logistics->grievance
    insert chain succeeds end-to-end (the "Minimum Viable Demo" loop from
    the architecture blueprint, at the data layer).
  - db/seed_from_json.py loads every existing JSON demo file with the
    exact row counts those files contain.
  - Migration downgrade + re-upgrade round-trips cleanly.
"""

import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+psycopg2://farmlink:farmlink@localhost:5432/postgres",
)
TEST_DB_NAME = "farmlink_schema_pytest"
TEST_DB_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"

EXPECTED_TABLES = {
    "users", "fpos", "farmer_profiles", "fpo_members", "buyer_profiles",
    "commodities", "markets", "market_prices", "buyer_demands",
    "lots", "lot_images", "quality_grades",
    "offers", "transactions", "payments",
    "logistics_providers", "cold_storage_facilities", "logistics_bookings",
    "grievances", "audit_logs", "alembic_version",
}


def _run_alembic(*args):
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + list(args),
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    return result


@pytest.fixture(scope="module")
def test_db():
    admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()

    _run_alembic("upgrade", "head")

    engine = create_engine(TEST_DB_URL, future=True)
    yield engine
    engine.dispose()

    admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin_engine.connect() as conn:
        # Terminate any lingering connections (e.g. an engine created inside a
        # test that forgot to .dispose()) before dropping, or the DROP itself
        # fails with "database is being accessed by other users".
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :name AND pid <> pg_backend_pid()"
        ), {"name": TEST_DB_NAME})
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture()
def session(test_db):
    Session = sessionmaker(bind=test_db, future=True)
    s = Session()
    yield s
    s.rollback()
    s.close()


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------

def test_all_expected_tables_exist(test_db):
    inspector = sa.inspect(test_db)
    actual_tables = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - actual_tables
    assert not missing, f"Missing tables after migration: {missing}"


def test_migration_history_has_two_revisions():
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "history"],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert "initial schema" in result.stdout
    assert "transaction state machine trigger" in result.stdout


# ---------------------------------------------------------------------------
# Constraint enforcement
# ---------------------------------------------------------------------------

def test_market_price_bounds_check_constraint_rejects_bad_data(session):
    from db.models import Commodity, Market, MarketPrice

    c = Commodity(name="TestCropA")
    m = Market(name="Test APMC A", district="TestDistrict")
    session.add_all([c, m])
    session.flush()

    bad_price = MarketPrice(
        market_id=m.id, commodity_id=c.id, arrival_date="2026-09-01",
        min_price=100, max_price=50, modal_price=75,  # max < min: invalid
        source="SYNTHETIC",
    )
    session.add(bad_price)
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()


def test_lot_quantity_must_be_positive(session):
    from db.models import User, FarmerProfile, Commodity, Lot

    u = User(name="Test Farmer", phone="9990000001", password_hash="x", role="FARMER")
    session.add(u)
    session.flush()
    fp = FarmerProfile(user_id=u.id)
    c = Commodity(name="TestCropB")
    session.add_all([fp, c])
    session.flush()

    bad_lot = Lot(farmer_id=fp.id, commodity_id=c.id, quantity=-5, grade="A")
    session.add(bad_lot)
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()


def test_user_phone_must_be_unique(session):
    from db.models import User

    session.add(User(name="A", phone="9990000002", password_hash="x", role="FARMER"))
    session.flush()
    session.add(User(name="B", phone="9990000002", password_hash="y", role="BUYER"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_lot_rejects_nonexistent_farmer_fk(session):
    from db.models import Lot, Commodity

    c = Commodity(name="TestCropC")
    session.add(c)
    session.flush()
    session.add(Lot(farmer_id=999999, commodity_id=c.id, quantity=10, grade="A"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# Transaction state machine trigger
# ---------------------------------------------------------------------------

def _make_transaction_prereqs(session, suffix):
    from db.models import User, FarmerProfile, BuyerProfile, Commodity, Lot, Offer

    farmer_user = User(name=f"Farmer{suffix}", phone=f"777000{suffix}", password_hash="x", role="FARMER")
    session.add(farmer_user)
    session.flush()
    farmer = FarmerProfile(user_id=farmer_user.id)
    buyer = BuyerProfile(business_name=f"Buyer{suffix}", buyer_type="TRADER")
    commodity = Commodity(name=f"Crop{suffix}")
    session.add_all([farmer, buyer, commodity])
    session.flush()

    lot = Lot(farmer_id=farmer.id, commodity_id=commodity.id, quantity=20, grade="A", status="MATCHED")
    session.add(lot)
    session.flush()

    offer = Offer(lot_id=lot.id, buyer_id=buyer.id, offer_price_per_quintal=30, quantity_quintals=20,
                  status="ACCEPTED")
    session.add(offer)
    session.flush()
    return farmer, buyer, lot, offer


def test_transaction_must_be_created_in_offer_accepted_state(session):
    from db.models import Transaction

    farmer, buyer, lot, offer = _make_transaction_prereqs(session, "1")
    txn = Transaction(
        lot_id=lot.id, offer_id=offer.id, farmer_id=farmer.id, buyer_id=buyer.id,
        quantity_quintals=20, agreed_price_per_quintal=30, gross_revenue=600,
        net_realization=580, status="COMPLETED",  # illegal initial state
    )
    session.add(txn)
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()


def test_transaction_rejects_illegal_state_jump(session):
    from db.models import Transaction

    farmer, buyer, lot, offer = _make_transaction_prereqs(session, "2")
    txn = Transaction(
        lot_id=lot.id, offer_id=offer.id, farmer_id=farmer.id, buyer_id=buyer.id,
        quantity_quintals=20, agreed_price_per_quintal=30, gross_revenue=600, net_realization=580,
    )
    session.add(txn)
    session.flush()  # starts in OFFER_ACCEPTED

    txn.status = "DELIVERED"  # skips PAYMENT_INITIATED and LOGISTICS_ARRANGED
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()


def test_transaction_full_legal_state_sequence_succeeds(session):
    from db.models import Transaction

    farmer, buyer, lot, offer = _make_transaction_prereqs(session, "3")
    txn = Transaction(
        lot_id=lot.id, offer_id=offer.id, farmer_id=farmer.id, buyer_id=buyer.id,
        quantity_quintals=20, agreed_price_per_quintal=30, gross_revenue=600, net_realization=580,
    )
    session.add(txn)
    session.flush()

    for next_status in ["PAYMENT_INITIATED", "LOGISTICS_ARRANGED", "IN_TRANSIT",
                         "DELIVERED", "PAYMENT_CONFIRMED", "COMPLETED"]:
        txn.status = next_status
        session.flush()  # must not raise

    assert txn.status == "COMPLETED"


def test_completed_transaction_is_terminal(session):
    from db.models import Transaction

    farmer, buyer, lot, offer = _make_transaction_prereqs(session, "4")
    txn = Transaction(
        lot_id=lot.id, offer_id=offer.id, farmer_id=farmer.id, buyer_id=buyer.id,
        quantity_quintals=20, agreed_price_per_quintal=30, gross_revenue=600, net_realization=580,
    )
    session.add(txn)
    session.flush()
    for next_status in ["PAYMENT_INITIATED", "LOGISTICS_ARRANGED", "IN_TRANSIT",
                         "DELIVERED", "PAYMENT_CONFIRMED", "COMPLETED"]:
        txn.status = next_status
        session.flush()

    txn.status = "PAYMENT_INITIATED"  # trying to go backward out of a terminal state
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()


def test_cancelled_is_reachable_from_any_nonterminal_state(session):
    from db.models import Transaction

    farmer, buyer, lot, offer = _make_transaction_prereqs(session, "5")
    txn = Transaction(
        lot_id=lot.id, offer_id=offer.id, farmer_id=farmer.id, buyer_id=buyer.id,
        quantity_quintals=20, agreed_price_per_quintal=30, gross_revenue=600, net_realization=580,
    )
    session.add(txn)
    session.flush()
    txn.status = "PAYMENT_INITIATED"
    session.flush()
    txn.status = "CANCELLED"
    session.flush()  # must not raise
    assert txn.status == "CANCELLED"


# ---------------------------------------------------------------------------
# Full loop (blueprint's "Minimum Viable Demo")
# ---------------------------------------------------------------------------

def test_full_farmer_to_grievance_loop(session):
    from db.models import (
        User, FarmerProfile, BuyerProfile, Commodity, Lot, Offer, Transaction,
        Payment, LogisticsProvider, LogisticsBooking, Grievance,
    )

    farmer_user = User(name="Loop Farmer", phone="8887776660", password_hash="x", role="FARMER")
    session.add(farmer_user)
    session.flush()
    farmer = FarmerProfile(user_id=farmer_user.id, land_size=3.5, crops=["Onion"])
    buyer = BuyerProfile(business_name="Loop Buyer Co", buyer_type="PROCESSOR", reliability_score=4.2)
    commodity = Commodity(name="LoopOnion")
    provider = LogisticsProvider(name="Loop Transport", rate_per_km_per_quintal=1.2, avg_rating=4.5)
    session.add_all([farmer, buyer, commodity, provider])
    session.flush()

    lot = Lot(farmer_id=farmer.id, commodity_id=commodity.id, quantity=20, grade="A", status="MATCHED")
    session.add(lot)
    session.flush()

    offer = Offer(lot_id=lot.id, buyer_id=buyer.id, offer_price_per_quintal=30,
                  quantity_quintals=20, net_realization_at_offer=575, status="ACCEPTED")
    session.add(offer)
    session.flush()

    txn = Transaction(
        lot_id=lot.id, offer_id=offer.id, farmer_id=farmer.id, buyer_id=buyer.id,
        quantity_quintals=20, agreed_price_per_quintal=30, gross_revenue=600, net_realization=575,
    )
    session.add(txn)
    session.flush()

    payment = Payment(transaction_id=txn.id, amount=600, status="INITIATED")
    booking = LogisticsBooking(transaction_id=txn.id, provider_id=provider.id,
                                distance_km=42.0, distance_source="HAVERSINE_PROXY",
                                transport_cost_inr=25.0)
    session.add_all([payment, booking])
    session.flush()

    grievance = Grievance(
        ticket_id="GRV-20260910-TEST01", transaction_id=txn.id, filed_by_user_id=farmer_user.id,
        complaint_type="payment_not_received", matched_known_category=True,
        days_since_issue=4, escalated=True, severity="high",
    )
    session.add(grievance)
    session.flush()

    # Sanity: everything actually landed and is linked correctly.
    assert txn.lot_id == lot.id
    assert payment.transaction_id == txn.id
    assert booking.provider_id == provider.id
    assert grievance.transaction_id == txn.id


# ---------------------------------------------------------------------------
# Seed script
# ---------------------------------------------------------------------------

def test_seed_script_matches_json_file_row_counts(test_db):
    import json
    from db import seed_from_json

    result = seed_from_json.run(database_url=TEST_DB_URL)

    data_dir = os.path.join(BASE_DIR, "data")
    with open(os.path.join(data_dir, "mock_mandi_prices.json")) as f:
        expected_prices = len(json.load(f))
    with open(os.path.join(data_dir, "buyers_augmented.json")) as f:
        buyers = json.load(f)
    expected_buyers = len(buyers)
    with open(os.path.join(data_dir, "logistics_providers.json")) as f:
        expected_logistics = len(json.load(f))
    with open(os.path.join(data_dir, "cold_storage.json")) as f:
        expected_storage = len(json.load(f))

    assert result["market_prices"] == expected_prices
    assert result["buyers"] == expected_buyers
    assert result["buyers_source_file"] == "buyers_augmented.json"
    assert result["logistics_providers"] == expected_logistics
    assert result["cold_storage_facilities"] == expected_storage

    with test_db.connect() as conn:
        actual_buyers = conn.execute(text("SELECT COUNT(*) FROM buyer_profiles")).scalar()
        assert actual_buyers == expected_buyers
        all_synthetic = conn.execute(
            text("SELECT COUNT(*) FROM market_prices WHERE source != 'SYNTHETIC'")
        ).scalar()
        assert all_synthetic == 0, "Seeded mandi prices must be tagged SYNTHETIC, not misrepresented as live"

        # REAL BUG FOUND during DB-API integration work: seed_buyers() used
        # to insert BuyerProfile rows only, silently dropping
        # commodities_wanted/typical_volume_quintals -- leaving
        # buyer_demands completely empty after seeding, and every existing
        # test here passed anyway because none of them checked this table.
        # (Checked in this same test, not a separate one, because test_db
        # is module-scoped and seed_from_json.run() can only be called
        # once per module run without hitting unique-constraint violations
        # on a second insert of the same buyers -- see git history/
        # AUDIT_REPORT.md COMPONENT #2 for the failed first attempt.)
        expected_demand_rows = sum(len(b["commodities_wanted"]) for b in buyers)
        actual_demands = conn.execute(text("SELECT COUNT(*) FROM buyer_demands")).scalar()
        assert actual_demands == expected_demand_rows
        assert actual_demands > 0, "buyer_demands must not be empty after seeding"

        b0001 = next(b for b in buyers if b["buyer_id"] == "B0001")
        rows = conn.execute(text("""
            SELECT c.name, bd.required_quantity
            FROM buyer_demands bd
            JOIN buyer_profiles bp ON bp.id = bd.buyer_id
            JOIN commodities c ON c.id = bd.commodity_id
            WHERE bp.external_buyer_code = 'B0001'
            ORDER BY c.name
        """)).fetchall()
        found_commodities = sorted(r[0] for r in rows)
        assert found_commodities == sorted(b0001["commodities_wanted"])
        assert all(float(r[1]) == b0001["typical_volume_quintals"] for r in rows)


# ---------------------------------------------------------------------------
# Migration reversibility
# ---------------------------------------------------------------------------

def test_downgrade_then_upgrade_roundtrip():
    _run_alembic("downgrade", "base")
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        [sys.executable, "-c",
         "import sqlalchemy as sa; e=sa.create_engine('" + TEST_DB_URL + "'); "
         "insp=sa.inspect(e); print(len(insp.get_table_names()))"],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.stdout.strip() in ("0", "1"), \
        f"expected only alembic_version (or nothing) left after full downgrade, got: {result.stdout}"

    _run_alembic("upgrade", "head")
    engine = create_engine(TEST_DB_URL, future=True)
    try:
        inspector = sa.inspect(engine)
        assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))
    finally:
        engine.dispose()

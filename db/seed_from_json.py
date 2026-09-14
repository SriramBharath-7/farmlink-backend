"""
db/seed_from_json.py
-----------------------
Loads the project's existing flat-file demo data (data/buyers_augmented.json
if present else data/buyers.json, data/mock_mandi_prices.json,
data/logistics_providers.json, data/cold_storage.json) into the new
Postgres schema.

This exists to prove the schema in db/models.py actually holds the real
shapes the deterministic core produces -- not just an abstract design on
paper. It is NOT the production ingestion pipeline (blueprint section 24
describes a proper FETCHER -> VALIDATOR -> NORMALIZER -> DEDUPLICATOR
pipeline for live Agmarknet ingestion; this script is a one-shot demo-data
loader only, and every row it writes gets an explicit provenance tag so
that's never ambiguous later).

Usage:
    DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db \
        python3 -m db.seed_from_json
"""

import json
import os
from datetime import datetime

from sqlalchemy import select

from db.models import (
    Base, Commodity, Market, MarketPrice, BuyerProfile, BuyerDemand,
    LogisticsProvider, ColdStorageFacility,
)
from db.session import get_engine, get_session_factory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_or_create_commodity(session, cache, name):
    if name in cache:
        return cache[name]
    existing = session.scalar(select(Commodity).where(Commodity.name == name))
    if existing:
        cache[name] = existing
        return existing
    c = Commodity(name=name)
    session.add(c)
    session.flush()
    cache[name] = c
    return c


def _get_or_create_market(session, cache, name, district, state="Maharashtra"):
    key = (name, district)
    if key in cache:
        return cache[key]
    existing = session.scalar(
        select(Market).where(Market.name == name, Market.district == district)
    )
    if existing:
        cache[key] = existing
        return existing
    m = Market(name=name, apmc_name=name, district=district, state=state)
    session.add(m)
    session.flush()
    cache[key] = m
    return m


def seed_market_prices(session):
    records = _load_json("mock_mandi_prices.json")
    commodity_cache = {}
    market_cache = {}
    inserted = 0
    for r in records:
        commodity = _get_or_create_commodity(session, commodity_cache, r["commodity"])
        market = _get_or_create_market(session, market_cache, r["market"], r["district"], r["state"])
        arrival_date = datetime.strptime(r["arrival_date"], "%d/%m/%Y").date()
        session.add(MarketPrice(
            market_id=market.id,
            commodity_id=commodity.id,
            arrival_date=arrival_date,
            min_price=r["min_price"],
            max_price=r["max_price"],
            modal_price=r["modal_price"],
            source="SYNTHETIC",  # see AUDIT_REPORT.md: no DATA_GOV_IN_API_KEY configured in this
                                  # environment, so mandi_price_tool.py's own fallback path is what
                                  # actually produced data/mock_mandi_prices.json. Tagging this
                                  # anything but SYNTHETIC would misrepresent the source.
        ))
        inserted += 1
    return inserted


def seed_buyers(session):
    """
    REAL BUG FIXED HERE (found during DB-API integration work, missed by
    both prior sessions' testing): this function used to insert
    BuyerProfile rows only, silently dropping `commodities_wanted` and
    `typical_volume_quintals` from the source JSON entirely -- meaning
    buyer_demands (the table that actually says which commodities a
    buyer wants and how much) was left completely empty after seeding.
    test_seed_script_matches_json_file_row_counts only checked
    buyer/price/logistics/storage counts, so this gap passed every
    existing test while quietly making the DB unusable for buyer
    matching. Now returns buyer_demands_inserted too, and a dedicated
    test checks it against the source JSON, not just a total count.
    """
    filename = "buyers_augmented.json" if os.path.exists(os.path.join(DATA_DIR, "buyers_augmented.json")) \
        else "buyers.json"
    records = _load_json(filename)
    inserted = 0
    demands_inserted = 0
    commodity_cache = {}
    type_map = {
        "trader": "TRADER", "processor": "PROCESSOR",
        "exporter": "EXPORTER", "institutional": "INSTITUTION",
    }
    for r in records:
        registered_since = None
        if r.get("registered_since"):
            registered_since = datetime.strptime(r["registered_since"], "%Y-%m-%d").date()
        buyer = BuyerProfile(
            external_buyer_code=r["buyer_id"],
            business_name=r["name"],
            buyer_type=type_map.get(r["type"], "TRADER"),
            verification_status="VERIFIED" if r.get("kyc_verified") else "PENDING",
            reliability_score=r.get("reliability_score"),
            location=r.get("district"),
            payment_terms=r.get("payment_terms"),
            target_price_index=r.get("target_price_index"),
            minimum_grade_accepted=r.get("minimum_grade_accepted"),
            past_deals_completed=r.get("past_deals_completed", 0),
            past_deals_disputed=r.get("past_deals_disputed", 0),
            registered_since=registered_since,
            kyc_verified=bool(r.get("kyc_verified", False)),
            data_status=r.get("data_status", "SYNTHETIC"),
        )
        session.add(buyer)
        session.flush()  # need buyer.id before writing buyer_demands rows
        inserted += 1

        for commodity_name in r.get("commodities_wanted", []):
            commodity = _get_or_create_commodity(session, commodity_cache, commodity_name)
            session.add(BuyerDemand(
                buyer_id=buyer.id,
                commodity_id=commodity.id,
                required_quantity=r.get("typical_volume_quintals", 0),
                minimum_grade=r.get("minimum_grade_accepted"),
                status="OPEN",
                # target_price is intentionally left NULL: this project's
                # pricing model (tools/matching_engine.py) derives a buyer's
                # offer price dynamically as target_price_index * that
                # day's mandi modal price, not a fixed stored figure --
                # storing a snapshot here would go stale the moment mandi
                # prices move. target_price_index lives on buyer_profiles,
                # not here (documented deviation, see db/models.py docstring).
            ))
            demands_inserted += 1

    return inserted, demands_inserted, filename


def seed_logistics_providers(session):
    records = _load_json("logistics_providers.json")
    for r in records:
        session.add(LogisticsProvider(
            external_provider_code=r["provider_id"],
            name=r["name"],
            vehicle_types=r["vehicle_types"],
            rate_per_km_per_quintal=r["rate_per_km_per_quintal"],
            base_district=r["base_district"],
            avg_rating=r["avg_rating"],
            data_status="SYNTHETIC",
        ))
    return len(records)


def seed_cold_storage(session):
    records = _load_json("cold_storage.json")
    for r in records:
        session.add(ColdStorageFacility(
            external_facility_code=r["facility_id"],
            name=r["name"],
            district=r["district"],
            capacity_quintals=r["capacity_quintals"],
            available_capacity_pct=r["available_capacity_pct"],
            rate_per_quintal_per_day=r["rate_per_quintal_per_day"],
            supports_crops=r["supports_crops"],
            data_status="SYNTHETIC",
        ))
    return len(records)


def run(database_url=None):
    Session = get_session_factory(database_url)
    session = Session()
    try:
        prices = seed_market_prices(session)
        buyers, buyer_demands, buyers_source_file = seed_buyers(session)
        logistics = seed_logistics_providers(session)
        storage = seed_cold_storage(session)
        session.commit()
        return {
            "market_prices": prices,
            "buyers": buyers,
            "buyer_demands": buyer_demands,
            "buyers_source_file": buyers_source_file,
            "logistics_providers": logistics,
            "cold_storage_facilities": storage,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        # REAL BUG FIXED HERE (found while writing test_repositories.py --
        # took two attempts to get right, both documented since the first
        # "fix" was itself wrong): session.close() only returns the
        # connection to SQLAlchemy's pool, it does NOT close the
        # underlying TCP connection -- invisible for a one-shot CLI
        # script (the process exits and the OS reclaims the socket), but
        # a real leak when run() is called as a library function inside
        # a longer-lived process (a test suite, or a future admin
        # endpoint). FIRST attempt at a fix called get_engine(database_url)
        # separately and disposed THAT -- but get_session_factory() calls
        # get_engine() internally too, creating a SECOND, different engine
        # instance bound to the session, which was never disposed. The
        # actual fix: get the engine the session is really bound to, via
        # session.get_bind(), and dispose that one.
        engine = session.get_bind()
        session.close()
        engine.dispose()


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))

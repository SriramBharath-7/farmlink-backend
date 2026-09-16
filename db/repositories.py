"""
repositories.py
------------------
Postgres-backed data access, returning plain dicts shaped EXACTLY like
the JSON-file path already produces -- so forecast_tool.py,
matching_engine.py, and finance_tool.py need zero changes to work
against real database rows instead of parsed JSON. This is what makes
decision_pipeline.py's data_source_mode="postgres" additive rather than
a parallel reimplementation.

Every function here takes an already-open SQLAlchemy Session -- this
module doesn't manage engine/connection lifecycle itself (that's
db/session.py's job), so it's trivially testable against any session,
including a throwaway test database.
"""

from typing import List, Dict, Optional
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.models import Commodity, Market, MarketPrice, BuyerProfile, BuyerDemand, IngestionRun


def get_price_records(
    session: Session,
    commodity: str,
    district: str = "",
    state: str = "",
) -> List[Dict]:
    """
    Return market-price records for forecasting.

    Source hierarchy:
    1. Prefer AGMARKNET observations when available.
    2. Use SYNTHETIC observations only when no AGMARKNET observations
       exist for the requested commodity/location.

    Location filters:
    - district narrows results to the farmer's district.
    - state ensures national data does not accidentally cross state boundaries.

    REAL and SYNTHETIC observations are never mixed in one forecast.
    """

    def build_query(source: str):
        query = (
            select(
                MarketPrice,
                Commodity.name,
                Market.district,
                Market.state,
                Market.name,
            )
            .join(Commodity, MarketPrice.commodity_id == Commodity.id)
            .join(Market, MarketPrice.market_id == Market.id)
            .where(
                Commodity.name.ilike(commodity),
                MarketPrice.source == source,
            )
        )

        if state:
            query = query.where(Market.state.ilike(state))

        if district:
            query = query.where(Market.district.ilike(district))

        return query

    # Production authority: persisted government observations.
    rows = session.execute(build_query("AGMARKNET")).all()

    # Explicit database fallback only when no government observations
    # exist for this commodity/location.
    if not rows:
        rows = session.execute(build_query("SYNTHETIC")).all()

    records = []

    for price, commodity_name, market_district, market_state, market_name in rows:
        records.append({
            "commodity": commodity_name,
            "district": market_district,
            "state": market_state,
            "market": market_name,
            "arrival_date": price.arrival_date.strftime("%d/%m/%Y"),
            "modal_price": float(price.modal_price),
            "min_price": float(price.min_price),
            "max_price": float(price.max_price),
            "data_status": price.source,
        })

    return records


def get_market_opportunities(session: Session, commodity: str) -> List[Dict]:
    """
    Return REAL AGMARKNET market observations that can be considered as
    selling destinations for the requested commodity.

    Unlike get_price_records(), this discovery query deliberately has
    NO SYNTHETIC fallback. Market discovery must only advertise
    opportunities that are backed by persisted government observations.

    The result includes state, district, market, date, and price fields
    so the discovery layer can group/forecast/rank candidate markets
    without requiring the farmer to choose a destination beforehand.
    """
    rows = session.execute(
        select(
            MarketPrice,
            Commodity.name,
            Market.state,
            Market.district,
            Market.name,
        )
        .join(Commodity, MarketPrice.commodity_id == Commodity.id)
        .join(Market, MarketPrice.market_id == Market.id)
        .where(
            Commodity.name.ilike(commodity),
            MarketPrice.source == "AGMARKNET",
            Market.state.is_not(None),
            Market.district.is_not(None),
        )
        .order_by(
            MarketPrice.arrival_date.desc(),
            Market.state,
            Market.district,
            Market.name,
        )
    ).all()

    opportunities = []

    for price, commodity_name, market_state, market_district, market_name in rows:
        state_name = (market_state or "").strip()
        district_name = (market_district or "").strip()
        market_name_clean = (market_name or "").strip()

        if not state_name or not district_name:
            continue

        opportunities.append({
            "market_id": str(price.market_id),
            "commodity_id": str(price.commodity_id),
            "observation_id": str(price.id),
            "observation_date": price.arrival_date.isoformat(),
            "variety": price.variety,
            "grade": price.grade,
            "unit": price.unit,
            "commodity": commodity_name,
            "state": state_name,
            "district": district_name,
            "market": market_name_clean,
            "arrival_date": price.arrival_date.strftime("%d/%m/%Y"),
            "modal_price": float(price.modal_price),
            "min_price": float(price.min_price),
            "max_price": float(price.max_price),
            "data_status": "AGMARKNET",
        })

    return opportunities


def get_market_locations(session: Session) -> List[Dict]:
    """
    Return canonical state -> district choices represented by persisted
    REAL AGMARKNET observations.

    SYNTHETIC rows are deliberately excluded so the farmer-facing
    location selector only advertises locations backed by government
    market-price data. Values come directly from Market.state and
    Market.district, preventing frontend spelling drift.
    """
    rows = session.execute(
        select(Market.state, Market.district)
        .join(MarketPrice, MarketPrice.market_id == Market.id)
        .where(
            MarketPrice.source == "AGMARKNET",
            Market.state.is_not(None),
            Market.district.is_not(None),
        )
        .distinct()
        .order_by(Market.state, Market.district)
    ).all()

    districts_by_state: Dict[str, set] = {}

    for state, district in rows:
        state_name = (state or "").strip()
        district_name = (district or "").strip()

        if not state_name or not district_name:
            continue

        districts_by_state.setdefault(state_name, set()).add(district_name)

    return [
        {
            "state": state,
            "districts": sorted(districts, key=str.casefold),
        }
        for state, districts in sorted(
            districts_by_state.items(),
            key=lambda item: item[0].casefold(),
        )
    ]


def get_candidate_buyers(session: Session, commodity: str, kyc_verified_only: bool = True) -> List[Dict]:
    """
    Returns buyer dicts shaped exactly like buyers_augmented.json
    entries -- commodities_wanted is reconstructed as a single-item list
    (just the commodity this query asked about, via the buyer_demands
    join), which is all matching_engine.py needs since it only ever
    checks membership of the one commodity being matched, not the
    buyer's full demand list.

    typical_volume_quintals comes from THIS commodity's buyer_demands
    row specifically (a buyer can have different required_quantity per
    commodity in the relational model, unlike the flat JSON's single
    typical_volume_quintals scalar shared across all its commodities --
    this is actually more correct than the JSON source, not a
    simplification).
    """
    query = (
        select(BuyerProfile, BuyerDemand.required_quantity)
        .join(BuyerDemand, BuyerDemand.buyer_id == BuyerProfile.id)
        .join(Commodity, BuyerDemand.commodity_id == Commodity.id)
        .where(Commodity.name.ilike(commodity))
    )
    if kyc_verified_only:
        query = query.where(BuyerProfile.kyc_verified.is_(True))

    rows = session.execute(query).all()
    buyers = []
    for buyer, required_quantity in rows:
        buyers.append({
            "buyer_id": buyer.external_buyer_code,
            "name": buyer.business_name,
            "district": buyer.location,
            "commodities_wanted": [commodity],
            "typical_volume_quintals": float(required_quantity),
            "reliability_score": float(buyer.reliability_score) if buyer.reliability_score is not None else 0.0,
            "target_price_index": float(buyer.target_price_index) if buyer.target_price_index is not None else 1.0,
            "minimum_grade_accepted": buyer.minimum_grade_accepted or "C",
            "kyc_verified": buyer.kyc_verified,
        })
    return buyers


def get_market_data_status(session: Session) -> Dict:
    """
    Return aggregate health for persisted REAL AGMARKNET market data.

    Rules:
    - SYNTHETIC rows never contribute to availability or freshness.
    - UNAVAILABLE: no AGMARKNET MarketPrice rows exist.
    - FRESH: REAL rows exist and every represented state's latest
      AGMARKNET ingestion attempt is SUCCESS.
    - PARTIAL: REAL rows exist but at least one represented state's
      latest AGMARKNET ingestion attempt is non-fresh.

    Freshness is evaluated independently per state. A later SUCCESS for
    one state therefore cannot hide a FAILED latest attempt for another.
    """

    real_summary = session.execute(
        select(
            func.count(MarketPrice.id),
            func.count(func.distinct(Market.state)),
            func.max(MarketPrice.arrival_date),
        )
        .select_from(MarketPrice)
        .join(Market, MarketPrice.market_id == Market.id)
        .where(MarketPrice.source == "AGMARKNET")
    ).one()

    records_available = int(real_summary[0] or 0)
    states_with_real_data = int(real_summary[1] or 0)
    latest_data_date = real_summary[2]

    represented_states = set(
        session.execute(
            select(Market.state)
            .join(MarketPrice, MarketPrice.market_id == Market.id)
            .where(MarketPrice.source == "AGMARKNET")
            .distinct()
        ).scalars().all()
    )

    runs = session.execute(
        select(IngestionRun)
        .where(IngestionRun.source == "AGMARKNET")
        .order_by(IngestionRun.id.desc())
    ).scalars().all()

    latest_run_by_state = {}
    for run in runs:
        if run.state not in latest_run_by_state:
            latest_run_by_state[run.state] = run

    successful_completion_times = [
        run.completed_at
        for run in runs
        if run.status == "SUCCESS" and run.completed_at is not None
    ]
    last_successful_sync = (
        max(successful_completion_times)
        if successful_completion_times
        else None
    )

    failed_states = sorted(
        state
        for state in represented_states
        if (
            state in latest_run_by_state
            and latest_run_by_state[state].status == "FAILED"
        )
    )

    fresh_state_names = {
        state
        for state in represented_states
        if (
            state in latest_run_by_state
            and latest_run_by_state[state].status == "SUCCESS"
        )
    }

    fresh_states = len(fresh_state_names)
    stale_states = max(states_with_real_data - fresh_states, 0)

    if records_available == 0:
        status = "UNAVAILABLE"
    elif stale_states > 0 or failed_states:
        status = "PARTIAL"
    else:
        status = "FRESH"

    return {
        "source": "AGMARKNET",
        "status": status,
        "last_successful_sync": (
            last_successful_sync.isoformat()
            if last_successful_sync is not None
            else None
        ),
        "latest_data_date": (
            latest_data_date.isoformat()
            if latest_data_date is not None
            else None
        ),
        "records_available": records_available,
        "states_with_real_data": states_with_real_data,
        "fresh_states": fresh_states,
        "stale_states": stale_states,
        "failed_states": failed_states,
    }


def get_market_history(session: Session, market_id: int, commodity_id: int, observation_id: int):
    """Bounded REAL-only history, plus the exact selected observation (never substituted)."""
    query = (select(MarketPrice, Market.name, Market.state, Market.district, Commodity.name)
             .join(Market, Market.id == MarketPrice.market_id)
             .join(Commodity, Commodity.id == MarketPrice.commodity_id)
             .where(MarketPrice.market_id == market_id, MarketPrice.commodity_id == commodity_id,
                    MarketPrice.source == "AGMARKNET"))
    selected = session.execute(query.where(MarketPrice.id == observation_id)).first()
    if selected is None:
        return None

    def serialize(row):
        price, market, state, district, commodity = row
        return {
            "market_id": str(price.market_id), "commodity_id": str(price.commodity_id),
            "observation_id": str(price.id), "observation_date": price.arrival_date.isoformat(),
            "market": market, "state": state, "district": district, "commodity": commodity,
            "modal_price": float(price.modal_price) if price.modal_price is not None else None,
            "min_price": float(price.min_price) if price.min_price is not None else None,
            "max_price": float(price.max_price) if price.max_price is not None else None,
            "variety": price.variety, "grade": price.grade, "unit": price.unit,
            "provenance": {"category": "REAL", "source": "AGMARKNET"},
        }

    rows = session.execute(query.order_by(MarketPrice.arrival_date.desc(), MarketPrice.id.desc()).limit(181)).all()
    return {"selected_observation": serialize(selected),
            "observations": [serialize(row) for row in reversed(rows[:180])],
            "truncated": len(rows) > 180, "limit": 180}

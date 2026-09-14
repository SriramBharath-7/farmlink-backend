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
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Commodity, Market, MarketPrice, BuyerProfile, BuyerDemand


def get_price_records(session: Session, commodity: str, district: str = "") -> List[Dict]:
    """
    Returns records shaped exactly like mock_mandi_prices.json entries:
    arrival_date as a "DD/MM/YYYY" string (matching forecast_tool.py's
    _parse_date expectation exactly), modal/min/max_price as floats.

    Args:
        commodity: case-insensitive match against commodities.name
        district: optional case-insensitive filter on markets.district;
            omit to search state-wide (mirrors the JSON path's behavior
            when decision_pipeline.py widens after insufficient_data)
    """
    query = (
        select(MarketPrice, Commodity.name, Market.district, Market.state, Market.name)
        .join(Commodity, MarketPrice.commodity_id == Commodity.id)
        .join(Market, MarketPrice.market_id == Market.id)
        .where(Commodity.name.ilike(commodity))
    )
    if district:
        query = query.where(Market.district.ilike(district))

    rows = session.execute(query).all()
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

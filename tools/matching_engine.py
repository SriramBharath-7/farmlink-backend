"""
matching_engine.py
---------------------
Deterministic buyer ranking. NO LLM CALLS HAPPEN HERE.

Implements the weighted match-score formula from the architecture
blueprint (section 12):
    Price 35% | Demand Fit 20% | Reliability 20% | Distance 10%
    | Quality 10% | Quantity 5%

Every sub-score is normalized to 0-100 BEFORE weighting, and the
weighted sum + net realization + final ranking are all plain arithmetic.
The LLM's only job downstream is to explain why the #1-ranked buyer won
-- it must not recompute or override this ranking.
"""

from typing import List, Dict, Optional
from tools.finance_tool import calculate_net_realization

WEIGHTS = {
    "price": 0.35,
    "demand_fit": 0.20,
    "reliability": 0.20,
    "distance": 0.10,
    "quality": 0.10,
    "quantity": 0.05,
}

GRADE_RANK = {"A": 3, "B": 2, "C": 1}

MAX_REASONABLE_DISTANCE_KM = 500  # beyond this, distance score floors at 0
MAX_RELIABILITY = 5.0  # buyers.json reliability_score is out of 5


def _price_score(buyer_offer_price: float, all_offer_prices: List[float]) -> float:
    """Min-max normalize this buyer's price against the candidate pool.
    Highest price in the pool = 100, lowest = 0. If all prices are equal,
    everyone scores 100 (no differentiation possible)."""
    lo, hi = min(all_offer_prices), max(all_offer_prices)
    if hi == lo:
        return 100.0
    return round((buyer_offer_price - lo) / (hi - lo) * 100, 2)


def _demand_fit_score(commodity: str, buyer_commodities: List[str], typical_volume: float, quantity_quintals: float) -> float:
    """Binary crop match (must want this commodity at all) combined with
    how comfortably the buyer's typical volume can absorb this lot."""
    wants_crop = commodity.lower() in [c.lower() for c in buyer_commodities]
    if not wants_crop:
        return 0.0
    if typical_volume <= 0:
        return 50.0
    ratio = quantity_quintals / typical_volume
    # A buyer whose typical volume comfortably exceeds the lot scores
    # highest; a buyer for whom this lot is far larger than they usually
    # handle scores lower (capacity risk), floored at 20 not 0 since
    # they still explicitly want the crop.
    if ratio <= 1.0:
        return 100.0
    return round(max(20.0, 100 - (ratio - 1) * 40), 2)


def _reliability_score(reliability_out_of_5: float) -> float:
    return round(min(100.0, (reliability_out_of_5 / MAX_RELIABILITY) * 100), 2)


def _distance_score(distance_km: Optional[float]) -> float:
    if distance_km is None:
        return 0.0
    return round(max(0.0, 100 * (1 - distance_km / MAX_REASONABLE_DISTANCE_KM)), 2)


def _quality_score(farmer_grade: str, buyer_minimum_grade: str) -> float:
    farmer_rank = GRADE_RANK.get(farmer_grade.upper(), 1)
    required_rank = GRADE_RANK.get(buyer_minimum_grade.upper(), 1)
    if farmer_rank < required_rank:
        return 0.0  # doesn't meet the buyer's stated minimum -- hard fail
    # meets or exceeds requirement; exact match scores highest to avoid
    # implying "better than needed" always wins (it doesn't help the buyer)
    return 100.0 if farmer_rank == required_rank else 85.0


def _quantity_score(quantity_quintals: float, typical_volume: float) -> float:
    if typical_volume <= 0:
        return 0.0
    ratio = min(quantity_quintals, typical_volume) / max(quantity_quintals, typical_volume)
    return round(ratio * 100, 2)


def calculate_match_score(
    buyer: Dict,
    commodity: str,
    farmer_grade: str,
    quantity_quintals: float,
    distance_km: Optional[float],
    transport_cost: float,
    all_candidate_offer_prices: List[float],
    mandi_modal_price: float,
) -> Dict:
    """
    Computes one buyer's full match breakdown. Caller (rank_matched_buyers
    below) is responsible for calling this once per candidate buyer and
    sorting by match_score.

    buyer must have: buyer_id, name, district, commodities_wanted,
    typical_volume_quintals, reliability_score, target_price_index,
    minimum_grade_accepted (see augment_buyer_data.py for the last two).
    """
    offer_price = round(mandi_modal_price * buyer["target_price_index"], 2)

    price_score = _price_score(offer_price, all_candidate_offer_prices)
    demand_fit_score = _demand_fit_score(
        commodity, buyer["commodities_wanted"], buyer["typical_volume_quintals"], quantity_quintals
    )
    reliability_score = _reliability_score(buyer["reliability_score"])
    distance_score = _distance_score(distance_km)
    quality_score = _quality_score(farmer_grade, buyer["minimum_grade_accepted"])
    quantity_score = _quantity_score(quantity_quintals, buyer["typical_volume_quintals"])

    match_score = round(
        price_score * WEIGHTS["price"]
        + demand_fit_score * WEIGHTS["demand_fit"]
        + reliability_score * WEIGHTS["reliability"]
        + distance_score * WEIGHTS["distance"]
        + quality_score * WEIGHTS["quality"]
        + quantity_score * WEIGHTS["quantity"],
        2,
    )

    net = calculate_net_realization(
        offer_price_per_quintal=offer_price,
        quantity_quintals=quantity_quintals,
        transport_cost=transport_cost,
    )

    return {
        "buyer_id": buyer["buyer_id"],
        "buyer_name": buyer["name"],
        "buyer_district": buyer["district"],
        "offer_price_per_quintal": offer_price,
        "meets_grade_requirement": quality_score > 0,
        "match_score": match_score,
        "score_breakdown": {
            "price_score": price_score,
            "demand_fit_score": demand_fit_score,
            "reliability_score": reliability_score,
            "distance_score": distance_score,
            "quality_score": quality_score,
            "quantity_score": quantity_score,
            "weights_applied": WEIGHTS,
        },
        "distance_km": distance_km,
        "transport_cost": transport_cost,
        "net_realization": net["net_realization"],
        "net_realization_per_quintal": net["net_realization_per_quintal"],
        "gross_revenue": net["gross_revenue"],
    }


def rank_matched_buyers(
    candidate_buyers: List[Dict],
    commodity: str,
    farmer_grade: str,
    quantity_quintals: float,
    mandi_modal_price: float,
    distance_and_cost_lookup,
) -> List[Dict]:
    """
    Full deterministic ranking pipeline.

    distance_and_cost_lookup: a callable(buyer_district) -> (distance_km,
    transport_cost) so this module stays decoupled from the logistics
    tools' I/O -- caller wires that in.

    Buyers that don't meet the grade requirement are still returned
    (transparency -- the farmer can see why they were excluded) but are
    sorted to the bottom regardless of score.
    """
    # First pass: compute offer prices so price_score can be normalized
    # against the full candidate pool, not one buyer in isolation.
    offer_prices = [round(mandi_modal_price * b["target_price_index"], 2) for b in candidate_buyers]

    results = []
    for buyer in candidate_buyers:
        distance_km, transport_cost = distance_and_cost_lookup(buyer["district"])
        result = calculate_match_score(
            buyer=buyer,
            commodity=commodity,
            farmer_grade=farmer_grade,
            quantity_quintals=quantity_quintals,
            distance_km=distance_km,
            transport_cost=transport_cost,
            all_candidate_offer_prices=offer_prices,
            mandi_modal_price=mandi_modal_price,
        )
        results.append(result)

    results.sort(key=lambda r: (r["meets_grade_requirement"], r["match_score"]), reverse=True)
    return results

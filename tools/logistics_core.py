"""
logistics_core.py
--------------------
Pure functions with the SAME logic as the existing logistics_tools.py
@tool functions, extracted so they can be:
  1. unit tested without installing/mocking crewai
  2. called directly by decision_pipeline.py / orchestrator_v2.py
  3. wrapped by the existing @tool decorators with zero logic duplication

This is the "tool contract" pattern from the blueprint (section 13):
input schema / output schema / failure behavior should not be entangled
with the agent framework. logistics_tools.py should become a thin
@tool wrapper that calls these functions -- see the bottom of this file
for exactly how.
"""

from typing import List, Dict, Optional
from tools.district_geo import distance_between_districts


def estimate_distance(origin_district: str, destination_district: str) -> Dict:
    """Same logic as distance_estimator() in logistics_tools.py."""
    straight_line = distance_between_districts(origin_district, destination_district)
    if straight_line is None:
        return {
            "error": f"Could not resolve one or both districts: '{origin_district}', '{destination_district}'",
            "origin": origin_district,
            "destination": destination_district,
        }
    road_estimate = round(straight_line * 1.25, 1)
    return {
        "origin": origin_district,
        "destination": destination_district,
        "straight_line_km": straight_line,
        "estimated_road_km": road_estimate,
        "distance_method": "haversine x1.25 (documented road-distance proxy, NOT actual routing)",
    }


def estimate_transport_cost(distance_km: float, quantity_quintals: float, providers: List[Dict], top_n: int = 5) -> Dict:
    """Same logic as transport_cost_estimator() in logistics_tools.py."""
    if distance_km is None or distance_km < 0:
        return {"error": "invalid_distance_km", "quotes": []}
    if quantity_quintals is None or quantity_quintals <= 0:
        return {"error": "invalid_quantity_quintals", "quotes": []}

    quotes = []
    for p in providers:
        est_cost = round(p["rate_per_km_per_quintal"] * distance_km * quantity_quintals, 2)
        quotes.append({
            "provider_id": p["provider_id"],
            "name": p["name"],
            "vehicle_types": p["vehicle_types"],
            "rating": p["avg_rating"],
            "estimated_total_cost_inr": est_cost,
            "cost_per_quintal": round(est_cost / quantity_quintals, 2) if quantity_quintals else 0.0,
        })
    quotes.sort(key=lambda q: q["estimated_total_cost_inr"])
    return {"quotes": quotes[:top_n], "cheapest_cost_inr": quotes[0]["estimated_total_cost_inr"] if quotes else None}


def lookup_storage(district: str, facilities: List[Dict], crop: str = "", min_capacity_pct: int = 10) -> Dict:
    """Same logic as storage_availability_lookup() in logistics_tools.py,
    but takes facilities as a parameter (testable) instead of reading a
    file internally."""
    matches = [
        f for f in facilities
        if f["district"].lower() == district.lower()
        and (not crop or crop.lower() in [c.lower() for c in f["supports_crops"]])
        and f["available_capacity_pct"] > min_capacity_pct
    ]
    widened = False
    if not matches:
        matches = [f for f in facilities if f["available_capacity_pct"] > min_capacity_pct][:5]
        widened = True
    return {"facilities": matches, "widened_search_statewide": widened}


# ---------------------------------------------------------------------
# How the existing crewai @tool functions in logistics_tools.py should
# be refactored to use this module (shown, not silently assumed):
#
#   from tools.logistics_core import estimate_distance, estimate_transport_cost, lookup_storage
#
#   @tool("Distance Estimator")
#   def distance_estimator(origin_district: str, destination_district: str) -> str:
#       """...(same docstring)..."""
#       return json.dumps(estimate_distance(origin_district, destination_district))
#
#   @tool("Transport Cost Estimator")
#   def transport_cost_estimator(distance_km: float, quantity_quintals: float) -> str:
#       """...(same docstring)..."""
#       providers = _load(LOGISTICS_PATH)
#       return json.dumps(estimate_transport_cost(distance_km, quantity_quintals, providers))
#
# No behavior changes -- this is a pure extraction, so existing agent
# behavior is unaffected, but the logic is now independently testable.
# ---------------------------------------------------------------------

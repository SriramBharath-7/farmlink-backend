"""
logistics_tools.py
--------------------
CrewAI tools for the Matching Agent and Logistics Agent:
  - distance_estimator: km between two districts
  - transport_cost_estimator: quotes from the synthetic transport operator
    pool for a given distance + quantity
  - storage_availability_lookup: nearby cold storage options if a farmer
    wants to hold rather than sell immediately

CHANGED vs. the original version of this file: the actual calculations
now live in tools/logistics_core.py as plain functions with no crewai
dependency, so they can be (and are) unit tested directly -- see
tests/test_logistics_core.py (8 tests, passing). This file is now a
thin @tool wrapper with NO logic duplicated here. Behavior is unchanged;
only the location of the logic moved.
"""

import os
import json
from crewai.tools import tool
from tools.logistics_core import estimate_distance, estimate_transport_cost, lookup_storage

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGISTICS_PATH = os.path.join(BASE_DIR, "data", "logistics_providers.json")
STORAGE_PATH = os.path.join(BASE_DIR, "data", "cold_storage.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@tool("Distance Estimator")
def distance_estimator(origin_district: str, destination_district: str) -> str:
    """
    Estimates road distance in km between two Maharashtra districts
    (straight-line/haversine, scaled up ~1.25x as a rough proxy for actual
    road distance). Used to estimate logistics cost and travel time between
    a farmer's lot location and a matched buyer.

    Args:
        origin_district: District where the lot/farmer is located.
        destination_district: District where the buyer is located.

    Returns:
        A JSON string with distance_km, or an error if a district isn't recognised.
    """
    return json.dumps(estimate_distance(origin_district, destination_district))


@tool("Transport Cost Estimator")
def transport_cost_estimator(distance_km: float, quantity_quintals: float) -> str:
    """
    Estimates transport cost and lists candidate transport operators for a
    given distance and quantity, drawn from the logistics provider pool.

    Args:
        distance_km: Estimated road distance in km.
        quantity_quintals: Quantity of produce to move, in quintals.

    Returns:
        A JSON string listing up to 5 operators with estimated total cost,
        sorted cheapest first.
    """
    providers = _load(LOGISTICS_PATH)
    return json.dumps(estimate_transport_cost(distance_km, quantity_quintals, providers), ensure_ascii=False)


@tool("Storage Availability Lookup")
def storage_availability_lookup(district: str, crop: str = "") -> str:
    """
    Looks up cold storage / warehousing facilities near a district that
    have available capacity, optionally filtered to those that support a
    given crop. Used when the Price Intelligence Agent recommends waiting
    rather than selling immediately.

    Args:
        district: District to search near.
        crop: Optional crop name to filter facilities that support it.

    Returns:
        A JSON string listing matching facilities with rate and available capacity.
    """
    facilities = _load(STORAGE_PATH)
    return json.dumps(lookup_storage(district, facilities, crop), ensure_ascii=False)

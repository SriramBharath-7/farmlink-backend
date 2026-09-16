"""Selected-market intelligence. No buyers, LLM, storage promises or invented history."""
from datetime import date
import json
import math
from tools.forecast_tool import forecast_price
from tools.location_lookup import normalize_location
from tools import osm_routing_tool as osm
from tools.adapters.osm_adapter import OSMRoutingAdapter
from tools.adapters.base_adapter import AdapterError
from tools.decision_pipeline import _cheapest_transport_quote, _load_json


def market_coordinates(state, district):
    """Read-only, zero HTTP; district geocodes are never exact mandi pins."""
    state, district = normalize_location(state or "", district or "")
    cache = osm._load_geocode_cache()
    coords = osm.cached_district_coordinates(state, district, cache)
    if coords is None:
        return {"latitude": None, "longitude": None, "coordinate_source": None, "coordinate_precision": "unknown"}
    key = json.dumps(["india", state.casefold(), district.casefold()])
    source = "OSM_GEOCODE_CACHE" if osm._valid_coords(cache.get(key)) else "STATIC_DISTRICT_COORDINATES"
    return {"latitude": coords[0], "longitude": coords[1], "coordinate_source": source,
            "coordinate_precision": "district_centroid"}


def decision_support(selected, observations, economics, today=None):
    """V1: >=3 comparable distinct recent dates; unchanged forecast's 1.5% trend rule.

    WAIT signals an observed rise, not a claim that holding beats selling. Storage,
    spoilage and future transport costs are not sufficiently known to return STORE.
    """
    today = today or date.today()
    identity = lambda row: (row["variety"], row["grade"], row["unit"])
    comparable = [row for row in observations if identity(row) == identity(selected)
                  and row["observation_date"] <= selected["observation_date"]]
    # A bounded history window can omit an older selected row. Do not invent support.
    if not any(row["observation_id"] == selected["observation_id"] for row in comparable):
        comparable = []
    dates = [date.fromisoformat(row["observation_date"]) for row in comparable]
    recent_dates = dates[-8:]
    age = (today - date.fromisoformat(selected["observation_date"])).days
    usable = all(row["modal_price"] is not None and math.isfinite(row["modal_price"]) and row["modal_price"] > 0 for row in comparable)
    limitations = ["Transport rates are synthetic estimates, not live quotes.",
                  "Storage, spoilage and holding costs are not validated; STORE is unavailable.",
                  "Observed mandi prices are not buyer offers; future prices are uncertain.",
                  "V1 timing support requires three comparable distinct dates, data at most 7 days old and no gap above 7 days."]
    reason = None
    if len(dates) < 3 or not usable:
        reason = "Fewer than three usable observations for the selected variety, grade and unit."
    elif len(set(dates)) != len(dates):
        reason = "Multiple comparable observations on a date make the timing signal ambiguous."
    elif any((b - a).days > 7 for a, b in zip(recent_dates, recent_dates[1:])):
        reason = "Recent comparable history has gaps above seven days."
    elif age < 0 or age > 7:
        reason = "The selected observation is future-dated or older than seven days."
    elif any(identity(row) == identity(selected) and row["observation_date"] > selected["observation_date"] for row in observations):
        reason = "Newer comparable observations exist; rediscover markets before acting."
    forecast = {"insufficient_data": True, "record_count": len(comparable), "reason": reason,
                "source": "Comparable REAL AGMARKNET observations", "data_status": "DERIVED"}
    if reason is None:
        forecast = {**forecast_price([{"arrival_date": date.fromisoformat(row["observation_date"]).strftime("%d/%m/%Y"), "modal_price": row["modal_price"]} for row in comparable]),
                    "source": "Comparable REAL AGMARKNET observations", "data_status": "DERIVED"}
    net = economics["estimated_net_realization"]
    if reason is None and net is None:
        reason = "Transport or unit economics are unavailable; net realization cannot be compared."
    if reason is None and net <= 0:
        reason = "Estimated net realization is not positive; a sell/wait recommendation is unsupported."
    action = "INSUFFICIENT_DATA"
    if reason is None:
        action = "WAIT" if forecast.get("trend") == "UPWARD" else "SELL_NOW"
        reason = ("Recent comparable observed prices are rising. Consider waiting, but holding costs and future net returns are unknown."
                  if action == "WAIT" else "Comparable prices are flat or falling and estimated current net is positive. Selling now avoids unpriced holding risk.")
    return {"recommended_action": action, "reason": reason,
            "data_sufficiency": "sufficient_for_heuristic" if action != "INSUFFICIENT_DATA" else "insufficient",
            "supporting_metrics": {**economics, "current_modal_price": selected["modal_price"],
                "comparable_observations": len(comparable), "observation_age_days": age, "as_of_date": today.isoformat(),
                "change_percent_vs_baseline": forecast.get("change_percent_vs_baseline"), "trend": forecast.get("trend")},
            "provenance": {"category": "DERIVED", "source": "deterministic_market_timing_v1"},
            "limitations": limitations}, forecast


def build_market_details(session, req):
    from db.repositories import get_market_history
    history = get_market_history(session, int(req.market_id), int(req.commodity_id), int(req.observation_id))
    if history is None:
        return None
    selected = history["selected_observation"]
    origin = normalize_location(req.farmer_state, req.farmer_district)
    destination = normalize_location(selected["state"] or "", selected["district"] or "")
    # Existing state-aware routing/fallbacks, one selected destination, unchanged budget.
    budget = osm.DiscoveryRoutingBudget()
    if origin == destination:
        distance, source = 0.0, "SAME_DISTRICT_PROXY"
    else:
        try:
            route = OSMRoutingAdapter(budget=budget).fetch(req.farmer_district, selected["district"],
                origin_state=req.farmer_state, destination_state=selected["state"])[0]
            distance, source = route["distance_km"], route["distance_source"]
        except AdapterError:
            distance, source = None, "UNRESOLVED_COORDINATES"
    cost, _ = _cheapest_transport_quote(distance, req.quantity_quintals, _load_json("logistics_providers.json"))
    unit_supported = (selected["unit"] or "").strip().casefold() in ("quintal", "quintals")
    gross = round(selected["modal_price"] * req.quantity_quintals, 2) if unit_supported and selected["modal_price"] is not None else None
    economics = {"distance_km": distance, "distance_source": source, "estimated_transport_cost": cost,
                 "gross_market_value": gross, "estimated_net_realization": round(gross - cost, 2) if gross is not None and cost is not None else None}
    decision, forecast = decision_support(selected, history["observations"], economics)
    variants = {(row["variety"], row["grade"], row["unit"]) for row in history["observations"]}
    usable_dates = {row["observation_date"] for row in history["observations"] if row["modal_price"] is not None}
    history.update(status="available" if len(usable_dates) >= 3 else "insufficient_history",
                   mixed_varieties_grades_units=len(variants) > 1,
                   note="REAL observations only; dates are not interpolated. Varieties, grades and units are preserved.")
    return {"market_id": selected["market_id"], "commodity_id": selected["commodity_id"],
            "selected_observation": selected, "history": history, "economics": economics,
            "forecast": forecast, "decision": decision,
            "coordinates": market_coordinates(selected["state"], selected["district"]),
            "routing_diagnostics": {"external_http_attempts": budget.attempts, "routing_budget_reason": budget.exhaustion_reason},
            "provenance": {"history": {"category": "REAL", "source": "AGMARKNET"},
                "distance": {"category": "DERIVED", "source": source},
                "forecast": {"category": "DERIVED", "source": "forecast_tool; comparable observed series"},
                "economics": {"category": "DERIVED", "source": "observed price minus estimated transport"},
                "transport_assumptions": {"category": "SYNTHETIC", "source": "logistics_providers.json"},
                "origin": {"category": "USER_DECLARED", "source": "farmer input"}},
            "limitations": ["Details retain the selected observation; newer history may exist.",
                "Economics are recalculated for this request using existing routing fallbacks.",
                "Coordinates describe a district centre, never a verified mandi entrance."]}

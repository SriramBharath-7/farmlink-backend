"""
decision_pipeline.py
-----------------------
Ties forecast_tool + matching_engine + finance_tool together into ONE
deterministic result for "farmer has X quintals of grade-G [crop] in
[district] -- what should they do?"

This is the concrete DATA -> TOOL -> CALCULATION -> STRUCTURED RESULT
chain. No LLM call happens anywhere in this file. The agent layer's only
remaining job is to translate this dict into a farmer-friendly sentence
(and into Marathi) -- it must copy the numbers verbatim, not recompute
or restate them differently.
"""

import json
import os
from datetime import datetime
from tools.forecast_tool import forecast_price
from tools.matching_engine import rank_matched_buyers
from tools.district_geo import distance_between_districts
from tools.adapters.base_adapter import AdapterError


def _osm_estimate(origin_district, dest_district):
    """Lazy import so decision_pipeline.py works even if `requests` isn't
    installed and use_live_routing is never set True -- keeps the
    haversine-only path dependency-free, matching the original design."""
    from tools.osm_routing_tool import estimate_district_to_district
    return estimate_district_to_district(origin_district, dest_district)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")


def _load_json(name):
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


def _cheapest_transport_quote(distance_km, quantity_quintals, providers):
    if distance_km is None:
        return None, None
    best = None
    for p in providers:
        cost = round(p["rate_per_km_per_quintal"] * distance_km * quantity_quintals, 2)
        if best is None or cost < best[1]:
            best = (p["provider_id"], cost)
    return (best[1], best[0]) if best else (None, None)


def build_sell_decision(commodity: str, district: str, quantity_quintals: float, grade: str = "A",
                         use_live_routing: bool = False, data_source_mode: str = "direct",
                         db_session=None, state: str = "") -> dict:
    """
    Returns a single structured dict covering:
      - price forecast (deterministic, from forecast_tool)
      - ranked buyer shortlist (deterministic, from matching_engine)
      - net realization per buyer (deterministic, from finance_tool via matching_engine)
    Every value carries a data_status so a judge/frontend can see what's
    live/synthetic/derived at a glance.

    Args:
        use_live_routing: if True, distance lookups try the real OSM
            Nominatim+OSRM path first (see osm_routing_tool.py), falling
            back to the haversine proxy on any failure. Defaults to
            False so existing tested behavior is unchanged unless this
            is explicitly opted into -- flip to True once
            osm_routing_tool.USER_AGENT has a real contact and the
            geocode cache has been precomputed (see
            precache_osm_district_coords.py).
        data_source_mode: "direct" (default, UNCHANGED behavior -- reads
            local JSON files) | "adapter" (routes through
            tools/adapters/registry.py's FallbackChain) | "postgres"
            (reads through db/repositories.py against a REAL database --
            requires db_session). Output SHAPE is identical across all
            three; test_decision_pipeline_adapter_mode.py and
            test_decision_pipeline_postgres_mode.py cross-check for
            consistency, so this genuinely proves each layer is a real,
            swappable data path and not parallel dead code.
        db_session: a live SQLAlchemy Session, REQUIRED when
            data_source_mode="postgres" (raises ValueError if omitted --
            deliberately not silently falling back to JSON, since that
            would defeat the entire point of asking for DB-backed data).
    """
    if data_source_mode not in ("direct", "adapter", "postgres"):
        raise ValueError("data_source_mode must be 'direct', 'adapter', or 'postgres'")
    if data_source_mode == "postgres" and db_session is None:
        raise ValueError("data_source_mode='postgres' requires db_session to be provided")

    price_source_label = "mock_mandi_prices.json (synthetic fallback dataset)"
    price_chain = None

    if data_source_mode == "adapter":
        from tools.adapters.registry import get_price_chain
        price_chain = get_price_chain()
        try:
            commodity_records, price_winner, _failed = price_chain.fetch(commodity=commodity, district=district)
            price_source_label = price_winner["source_name"]
        except AdapterError:
            commodity_records = []
    elif data_source_mode == "postgres":
        from db.repositories import get_price_records
        commodity_records = get_price_records(
            db_session,
            commodity,
            district=district,
            state=state,
        )
        price_source_label = "POSTGRES"
    else:
        mandi_records_all = _load_json("mock_mandi_prices.json")
        commodity_records = [
            r for r in mandi_records_all
            if r["commodity"].lower() == commodity.lower() and r["district"].lower() == district.lower()
        ]

    forecast = forecast_price(commodity_records)
    if data_source_mode in ("adapter", "postgres") and commodity_records:
        price_data_status = commodity_records[0]["data_status"]
    else:
        price_data_status = "SYNTHETIC"

    if forecast.get("insufficient_data"):
        # Widen to state-wide records for this commodity so the pipeline
        # degrades gracefully instead of returning nothing -- but this
        # MUST be flagged, not silently treated as district-specific.
        if data_source_mode == "adapter":
            try:
                commodity_records, price_winner, _failed = price_chain.fetch(commodity=commodity, district="")
                price_source_label = price_winner["source_name"]
                price_data_status = commodity_records[0]["data_status"] if commodity_records else price_data_status
            except AdapterError:
                commodity_records = []
        elif data_source_mode == "postgres":
            from db.repositories import get_price_records
            commodity_records = get_price_records(
                db_session,
                commodity,
                district="",
                state=state,
            )
            price_data_status = commodity_records[0]["data_status"] if commodity_records else price_data_status
        else:
            commodity_records = [r for r in mandi_records_all if r["commodity"].lower() == commodity.lower()]
        forecast = forecast_price(commodity_records)
        forecast["widened_to_statewide"] = True

    if forecast.get("insufficient_data"):
        return {
            "error": "insufficient_price_data",
            "commodity": commodity,
            "district": district,
            "data_status": "UNAVAILABLE",
        }

    mandi_modal_price = forecast["current_modal"]

    if data_source_mode == "postgres":
        from db.repositories import get_candidate_buyers
        candidates = get_candidate_buyers(db_session, commodity, kyc_verified_only=True)
    else:
        buyers = _load_json("buyers_augmented.json")
        candidates = [
            b for b in buyers
            if commodity.lower() in [c.lower() for c in b["commodities_wanted"]]
            and b.get("kyc_verified", False)
        ]

    providers = _load_json("logistics_providers.json")

    routing_chain = None
    if data_source_mode == "adapter":
        from tools.adapters.registry import get_routing_chain
        routing_sources = ["osm", "static_haversine"] if use_live_routing else ["static_haversine"]
        routing_chain = get_routing_chain(routing_sources)

    def lookup(buyer_district):
        if data_source_mode == "adapter":
            try:
                records, _winner, _failed = routing_chain.fetch(
                    origin_district=district, destination_district=buyer_district
                )
                road_est = records[0]["distance_km"]
            except AdapterError:
                road_est = None
            cost, _provider_id = _cheapest_transport_quote(road_est, quantity_quintals, providers)
            return road_est, (cost or 0.0)

        if use_live_routing:
            route = _osm_estimate(district, buyer_district)
            if "error" not in route:
                road_est = route["distance_km"]
                cost, _provider_id = _cheapest_transport_quote(road_est, quantity_quintals, providers)
                return road_est, (cost or 0.0)
        # default path: existing tested haversine proxy (unchanged behavior)
        dist = distance_between_districts(district, buyer_district)
        road_est = round(dist * 1.25, 1) if dist is not None else None
        cost, _provider_id = _cheapest_transport_quote(road_est, quantity_quintals, providers)
        return road_est, (cost or 0.0)

    ranked_buyers = rank_matched_buyers(
        candidate_buyers=candidates,
        commodity=commodity,
        farmer_grade=grade,
        quantity_quintals=quantity_quintals,
        mandi_modal_price=mandi_modal_price,
        distance_and_cost_lookup=lookup,
    )

    top_buyers = [b for b in ranked_buyers if b["meets_grade_requirement"]][:5]

    if forecast["trend"] == "UPWARD" and forecast["confidence"] >= 0.5:
        action = "WAIT"
    elif not top_buyers:
        action = "NO_VERIFIED_BUYER_MATCH"
    else:
        action = "SELL_NOW"

    return {
        "request": {
            "commodity": commodity,
            "state": state,
            "district": district,
            "quantity_quintals": quantity_quintals,
            "grade": grade,
        },
        "generated_at": datetime.now().isoformat(),
        "price_forecast": {**forecast, "data_status": price_data_status, "source": price_source_label},
        "recommended_action": action,
        "buyer_shortlist": top_buyers,
        "excluded_buyers_count": len(ranked_buyers) - len(top_buyers),
        "data_status_summary": {
            "price_data": price_data_status,
            "buyer_data": "POSTGRES (buyer_profiles + buyer_demands)" if data_source_mode == "postgres"
                          else "SYNTHETIC (augmented -- see augment_buyer_data.py)",
            "distance_data": (
                "ADAPTER_CHAIN (see FallbackChain result per-buyer)" if data_source_mode == "adapter"
                else ("LIVE_OSM_ATTEMPTED (falls back to haversine proxy on failure)" if use_live_routing
                      else "DERIVED (haversine x1.25 road-distance proxy)")
            ),
            "transport_cost": "SYNTHETIC (logistics_providers.json rate table)",
        },
    }


def build_market_discovery(
    commodity: str,
    farmer_state: str,
    farmer_district: str,
    quantity_quintals: float,
    db_session,
    limit: int = 10,
) -> dict:
    """
    Discover selling-market opportunities without asking the farmer to
    choose a destination first.

    REAL AGMARKNET observations are grouped by individual market. The
    latest observed price drives economics; forecasting is optional
    enrichment and insufficient history does not exclude a market. The
    farmer's district is used only as the produce/transport origin.

    Ranking is based on estimated net market realization:
        current modal price * quantity - estimated transport cost

    Distance uses state-aware OSM routing with the existing geographic fallback.
    Transport rates remain the existing SYNTHETIC logistics provider
    table and are labelled as such in the response. Opportunities whose
    distance cannot be estimated are still returned, but rank below
    opportunities with a calculable net realization.

    This is market discovery, not buyer discovery. Buyer matching remains
    a separate step after a promising selling opportunity is identified.
    """
    if db_session is None:
        raise ValueError("build_market_discovery requires db_session")

    if quantity_quintals <= 0:
        return {
            "error": "invalid_quantity",
            "reason": "quantity_quintals must be greater than zero",
        }

    from db.repositories import get_market_opportunities
    from tools.adapters.osm_adapter import OSMRoutingAdapter
    from tools.location_lookup import normalize_location

    records = get_market_opportunities(db_session, commodity)
    if not records:
        return {
            "error": "no_real_market_data",
            "commodity": commodity,
            "data_status": "UNAVAILABLE",
        }

    # Keep each physical mandi separate. A district can contain multiple
    # markets with different price histories.
    grouped = {}
    for record in records:
        key = (
            record["state"].casefold(),
            record["district"].casefold(),
            record["market"].casefold(),
        )
        grouped.setdefault(key, []).append(record)

    providers = _load_json("logistics_providers.json")
    opportunities = []
    origin_lookup = normalize_location(farmer_state, farmer_district)
    from tools.market_shortlist import shortlist_markets
    from tools.osm_routing_tool import DiscoveryRoutingBudget, _load_geocode_cache
    shortlist = shortlist_markets(grouped, origin_lookup, quantity_quintals, _load_geocode_cache())
    budget = DiscoveryRoutingBudget()
    routing = OSMRoutingAdapter(budget=budget)
    distance_cache = {}

    for candidate in shortlist:
        market_records = candidate["records"]
        current_modal = candidate["current_modal"]
        forecast = forecast_price(market_records)

        sample = candidate["observation"]
        market_ids = {str(r["market_id"]) for r in market_records if r.get("market_id") is not None}
        identity_ambiguous = len(market_ids) > 1
        identity_complete = all(sample.get(k) is not None for k in ("market_id", "commodity_id", "observation_id"))
        market_state = sample["state"]
        market_district = sample["district"]
        market_name = sample["market"]

        destination_lookup = normalize_location(market_state, market_district)
        same_district = origin_lookup == destination_lookup
        same_state = origin_lookup[0] == destination_lookup[0]

        if same_district:
            distance_km = 0.0
            distance_source = "SAME_DISTRICT_PROXY"
            budget_reason = None
        else:
            if destination_lookup not in distance_cache:
                try:
                    route = routing.fetch(
                        farmer_district, market_district,
                        origin_state=farmer_state, destination_state=market_state,
                    )[0]
                    distance_cache[destination_lookup] = (
                        route["distance_km"], route["distance_source"], route.get("budget_reason"),
                    )
                except AdapterError:
                    distance_cache[destination_lookup] = (None, "UNRESOLVED_COORDINATES", None)
            distance_km, distance_source, budget_reason = distance_cache[destination_lookup]

        transport_cost, provider_id = _cheapest_transport_quote(
            distance_km,
            quantity_quintals,
            providers,
        )

        gross_market_value = round(current_modal * quantity_quintals, 2)
        estimated_net_realization = (
            round(gross_market_value - transport_cost, 2)
            if transport_cost is not None
            else None
        )

        if same_district:
            scope = "SAME_DISTRICT"
        elif same_state:
            scope = "SAME_STATE"
        else:
            scope = "OTHER_STATE"

        opportunities.append({
            **{k: str(sample[k]) if sample.get(k) is not None else None
               for k in ("market_id", "commodity_id", "observation_id")},
            "observation_date": sample.get("observation_date") or datetime.strptime(sample["arrival_date"], "%d/%m/%Y").date().isoformat(),
            "commodity": sample.get("commodity", commodity),
            "variety": sample.get("variety"), "grade": sample.get("grade"), "unit": sample.get("unit"),
            "selection_available": identity_complete and not identity_ambiguous,
            "selection_unavailable_reason": "ambiguous_market_identity" if identity_ambiguous else (None if identity_complete else "missing_observation_identity"),
            "provenance": {
                "price_observation": {"category": "REAL", "source": "AGMARKNET"},
                "distance": {"category": "DERIVED", "source": distance_source},
                "economics": {"category": "DERIVED", "source": "observed price minus estimated transport"},
                "transport_assumptions": {"category": "SYNTHETIC", "source": "logistics_providers.json"},
                "origin": {"category": "USER_DECLARED", "source": "farmer input"},
            },
            "state": market_state,
            "district": market_district,
            "market": market_name,
            "scope": scope,
            "distance_km": distance_km,
            "distance_source": distance_source,
            "routing_budget_reason": budget_reason,
            "current_modal_price_per_quintal": current_modal,
            "gross_market_value": gross_market_value,
            "estimated_transport_cost": transport_cost,
            "estimated_net_realization": estimated_net_realization,
            "transport_provider_id": provider_id,
            "price_forecast": {
                **forecast,
                "data_status": "AGMARKNET",
                "source": "POSTGRES / AGMARKNET",
            },
        })

    # Markets with a calculable net realization rank first. Within that
    # set, economics decide the order, so a farther market can outrank a
    # local one only when its higher market value survives transport cost.
    opportunities.sort(
        key=lambda item: (
            item["estimated_net_realization"] is None,
            -(item["estimated_net_realization"] or 0.0),
            item["distance_km"] if item["distance_km"] is not None else float("inf"),
            item["state"].casefold(),
            item["district"].casefold(),
            item["market"].casefold(),
        )
    )

    return {
        "request": {
            "commodity": commodity,
            "farmer_state": farmer_state,
            "farmer_district": farmer_district,
            "quantity_quintals": quantity_quintals,
        },
        "generated_at": datetime.now().isoformat(),
        "markets_considered": len(opportunities),
        "discovery_diagnostics": {
            "total_eligible_markets": len(grouped),
            "shortlisted_markets": len(shortlist),
            "evaluated_markets": len(opportunities),
            "markets_with_distance": sum(o["distance_km"] is not None for o in opportunities),
            "candidate_cap": 12,
            "external_http_attempts": budget.attempts,
            "routing_budget_reason": budget.exhaustion_reason,
            "search_scope": "Bounded shortlist; not exhaustive national optimization",
        },
        "market_opportunities": opportunities[:max(1, limit)],
        "data_status_summary": {
            "market_price_data": "AGMARKNET (REAL, persisted in POSTGRES)",
            "distance_data": "DERIVED (OSM district routing; haversine x1.25 fallback; same-district zero proxy)",
            "transport_cost": "SYNTHETIC (logistics_providers.json rate table)",
        },
    }


def build_matching_result(commodity: str, district: str, quantity_quintals: float, grade: str = "A",
                           use_live_routing: bool = False) -> dict:
    """
    Buyer-ranking-only version of build_sell_decision, for callers that
    just want the shortlist (e.g. the /agents/match-buyers endpoint)
    without the sell/wait framing. Reuses the exact same forecast +
    matching_engine calls -- no separate arithmetic path to drift out of
    sync with build_sell_decision.
    """
    full = build_sell_decision(commodity, district, quantity_quintals, grade, use_live_routing)
    if full.get("error"):
        return full
    return {
        "request": full["request"],
        "generated_at": full["generated_at"],
        "reference_mandi_price": full["price_forecast"]["current_modal"],
        "buyer_shortlist": full["buyer_shortlist"],
        "excluded_buyers_count": full["excluded_buyers_count"],
        "data_status_summary": full["data_status_summary"],
    }


def build_logistics_result(origin_district: str, destination_district: str, quantity_quintals: float,
                            use_live_routing: bool = False) -> dict:
    """
    Deterministic logistics-only result: distance, ranked transport
    quotes, and a cold-storage alternative -- for the /agents/logistics
    endpoint. Same underlying calculations as decision_pipeline's buyer
    lookup, exposed standalone since a caller may want logistics for an
    already-agreed deal rather than as part of buyer discovery.
    """
    from tools.logistics_core import estimate_distance, estimate_transport_cost, lookup_storage

    providers = _load_json("logistics_providers.json")
    storage_facilities = _load_json("cold_storage.json")

    if use_live_routing:
        route = _osm_estimate(origin_district, destination_district)
        if "error" in route:
            return {"error": route["error"], "data_status": "UNAVAILABLE"}
        distance_km = route["distance_km"]
        distance_source = route["distance_source"]
    else:
        dist_result = estimate_distance(origin_district, destination_district)
        if "error" in dist_result:
            return {"error": dist_result["error"], "data_status": "UNAVAILABLE"}
        distance_km = dist_result["estimated_road_km"]
        distance_source = "HAVERSINE_PROXY"

    transport = estimate_transport_cost(distance_km, quantity_quintals, providers)
    storage = lookup_storage(origin_district, storage_facilities)

    return {
        "origin": origin_district,
        "destination": destination_district,
        "quantity_quintals": quantity_quintals,
        "distance_km": distance_km,
        "distance_source": distance_source,
        "transport_quotes": transport.get("quotes", []),
        "cheapest_transport_cost_inr": transport.get("cheapest_cost_inr"),
        "storage_alternative": storage,
        "data_status_summary": {
            "distance_data": distance_source,
            "transport_cost": "SYNTHETIC (logistics_providers.json rate table)",
            "storage_data": "SYNTHETIC (cold_storage.json)",
        },
    }


if __name__ == "__main__":
    result = build_sell_decision("Onion", "Nashik", 20, "A")
    print(json.dumps(result, indent=2, ensure_ascii=False))

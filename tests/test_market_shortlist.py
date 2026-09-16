from unittest.mock import Mock
import json
import pytest
import requests
from tools import osm_routing_tool as osm
from tools import decision_pipeline as pipeline
from tools.market_shortlist import shortlist_markets


def record(state, district, market, price=1000):
    return dict(state=state, district=district, market=market, modal_price=price,
                commodity="Onion", arrival_date="16/09/2026", data_status="AGMARKNET")


def grouped(rows):
    return {(r["state"].casefold(), r["district"].casefold(), r["market"].casefold()): [r] for r in rows}


def cached(state, district, lat, lon):
    return {json.dumps(["india", state.casefold(), district.casefold()]): {"lat": lat, "lon": lon}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(osm, "GEOCODE_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(osm, "_respect_rate_limit", lambda: None)
    http = Mock(side_effect=requests.Timeout("offline"))
    monkeypatch.setattr(osm.requests, "get", http)
    return http


def test_lanes_are_bounded_deterministic_and_network_free(isolated):
    rows = [record("Kerala", "Palakkad", f"Local{i}", 100+i) for i in range(5)]
    rows += [record("Karnataka", f"Nearby{i}", f"Nearby{i}", 200) for i in range(9)]
    rows += [record("Bihar", f"Far{i}", f"Far{i}", 10000+i) for i in range(100)]
    cache = cached("Kerala", "Palakkad", 10, 76)
    for i in range(9):
        cache.update(cached("Karnataka", f"Nearby{i}", 11+i/10, 76))
    chosen = shortlist_markets(grouped(rows), ("Kerala", "Palakkad"), 12, cache)
    assert len(chosen) == 12
    assert len({c["identity"] for c in chosen}) == 12
    assert all(c["location"][0] == "Kerala" for c in chosen[:3])
    # Remaining same-district candidates can enter geographic slots too.
    assert any(c["location"][0] == "Karnataka" for c in chosen)
    assert sum(c["location"][0] == "Bihar" for c in chosen) == 3
    assert [c["identity"] for c in chosen] == [c["identity"] for c in
        shortlist_markets(grouped(list(reversed(rows))), ("Kerala", "Palakkad"), 12, cache)]
    isolated.assert_not_called()


def test_empty_geographic_lane_redistributes_to_national(isolated):
    rows = [record("Bihar", str(i), str(i), i+1) for i in range(50)]
    result = shortlist_markets(grouped(rows), ("Kerala", "Palakkad"), 12, {})
    assert [c["current_modal"] for c in result] == list(range(50, 38, -1))
    isolated.assert_not_called()


@pytest.mark.parametrize("limit", [1, 10, 50])
def test_large_discovery_bounds_forecasts_http_and_reports_coverage(monkeypatch, isolated, limit):
    rows = [record("Bihar", f"District{i}", f"Mandi{i}", i+1) for i in range(200)]
    monkeypatch.setattr("db.repositories.get_market_opportunities", lambda *args: rows)
    def http(url, **kwargs):
        resp = Mock()
        resp.json.return_value = ([{"lat": "10", "lon": "76"}] if "nominatim" in url
                                  else {"code": "Ok", "routes": [{"distance": 100000, "duration": 60}]})
        return resp
    isolated.side_effect = http
    forecast = Mock(wraps=pipeline.forecast_price)
    monkeypatch.setattr(pipeline, "forecast_price", forecast)
    result = pipeline.build_market_discovery("Onion", "Kerala", "Palakkad", 12, object(), limit)
    d = result["discovery_diagnostics"]
    assert d["total_eligible_markets"] == 200
    assert d["shortlisted_markets"] == d["evaluated_markets"] == forecast.call_count == 12
    assert isolated.call_count == d["external_http_attempts"] == 12
    assert d["routing_budget_reason"] == "HTTP_BUDGET_EXHAUSTED"
    assert len(result["market_opportunities"]) == min(limit, 12)
    assert all(c.kwargs["allow_redirects"] is False for c in isolated.call_args_list)


def test_origin_failure_memoized_and_not_budget_failure(monkeypatch, isolated):
    rows = [record("Bihar", str(i), str(i)) for i in range(20)]
    monkeypatch.setattr("db.repositories.get_market_opportunities", lambda *args: rows)
    result = pipeline.build_market_discovery("Onion", "Kerala", "Palakkad", 12, object())
    assert isolated.call_count == 1
    assert result["discovery_diagnostics"]["routing_budget_reason"] is None
    assert all(o["distance_source"] == "UNRESOLVED_COORDINATES" for o in result["market_opportunities"])


def test_deadline_includes_rate_limit_wait_and_preserves_static_fallback(monkeypatch, isolated):
    now = [100.0]
    monkeypatch.setattr(osm.time, "monotonic", lambda: now[0])
    budget = osm.DiscoveryRoutingBudget(seconds=1)
    monkeypatch.setattr(osm, "_respect_rate_limit", lambda: now.__setitem__(0, 102))
    result = osm.estimate_district_to_district("Pune", "Nashik", budget=budget)
    isolated.assert_not_called()
    assert result["distance_km"] > 0
    assert result["distance_source"] == "HAVERSINE_FALLBACK"
    assert result["budget_reason"] == "DEADLINE_EXHAUSTED"


def test_remaining_time_caps_timeout_and_failures_consume_budget(isolated):
    budget = osm.DiscoveryRoutingBudget(max_attempts=1, seconds=0.5)
    osm.geocode_district("Unknown", "Kerala", budget=budget)
    assert 0 < isolated.call_args.kwargs["timeout"] <= 0.5
    result = osm.geocode_district("Other", "Kerala", budget=budget)
    assert result["budget_reason"] == "HTTP_BUDGET_EXHAUSTED"
    assert isolated.call_count == 1


def test_national_lane_can_rank_first_on_net(monkeypatch):
    from tools.adapters.osm_adapter import OSMRoutingAdapter
    rows = [record("Kerala", "Palakkad", "Local", 1000), record("Bihar", "Far", "National", 10000)]
    monkeypatch.setattr("db.repositories.get_market_opportunities", lambda *args: rows)
    monkeypatch.setattr(OSMRoutingAdapter, "fetch", Mock(return_value=[{"distance_km": 100, "distance_source": "OSRM_LIVE"}]))
    result = pipeline.build_market_discovery("Onion", "Kerala", "Palakkad", 12, object())
    assert result["market_opportunities"][0]["market"] == "National"


def test_api_serializes_coverage_and_budget_reason(monkeypatch):
    from fastapi.testclient import TestClient
    import api_v2
    rows = [record("Bihar", "Far", "Raw Mandi", 2000)]
    monkeypatch.setattr("db.repositories.get_market_opportunities", lambda *args: rows)
    monkeypatch.setattr(api_v2, "_open_db_session", lambda: Mock())
    factory = osm.DiscoveryRoutingBudget
    monkeypatch.setattr(osm, "DiscoveryRoutingBudget", lambda: factory(max_attempts=0))
    response = TestClient(api_v2.app).post("/agents/market-discovery", json={
        "crop": "Onion", "farmer_state": "Keralam", "farmer_district": "Palakad",
        "quantity_quintals": 12, "limit": 10,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["discovery_diagnostics"]["total_eligible_markets"] == 1
    assert body["discovery_diagnostics"]["external_http_attempts"] == 0
    item = body["market_opportunities"][0]
    assert item["routing_budget_reason"] == "HTTP_BUDGET_EXHAUSTED"
    assert item["distance_source"] == "HTTP_BUDGET_EXHAUSTED"
    assert item["distance_km"] is None
    assert item["price_forecast"]["insufficient_data"] is True


def test_cache_lookup_is_state_aware_and_does_not_use_legacy_keys(isolated):
    cache = {"Nashik": {"lat": 19, "lon": 73}}
    assert osm.cached_district_coordinates("Bihar", "Nashik", cache) is None
    assert osm.cached_district_coordinates("Maharashtra", "Nashik", cache) is not None
    isolated.assert_not_called()

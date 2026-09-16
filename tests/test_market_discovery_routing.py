"""National discovery regression tests: no database or live HTTP required."""
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
import requests

from tools import osm_routing_tool as osm
from tools import decision_pipeline as pipeline
from tools.location_lookup import normalize_location


@pytest.fixture(autouse=True)
def isolated_osm(monkeypatch, tmp_path):
    monkeypatch.setattr(osm, "GEOCODE_CACHE_PATH", str(tmp_path / "coords.json"))
    monkeypatch.setattr(osm, "_respect_rate_limit", lambda: None)
    http = Mock(side_effect=requests.Timeout("offline"))
    monkeypatch.setattr(osm.requests, "get", http)
    return http


def response(payload):
    result = Mock()
    result.json.return_value = payload
    return result


def history(state, district, market, price):
    return [dict(state=state, district=district, market=market,
                 commodity="Onion", modal_price=price, arrival_date=f"0{day}/09/2026")
            for day in (1, 2, 3)]


def discover(monkeypatch, records, state="Keralam", district="Palakad"):
    monkeypatch.setattr("db.repositories.get_market_opportunities", lambda session, crop: records)
    return pipeline.build_market_discovery("Onion", state, district, 12, object())


def test_state_aware_queries_cache_and_aliases(isolated_osm):
    isolated_osm.side_effect = lambda *a, **kw: response([{"lat": "10.7", "lon": "76.6"}])
    osm.geocode_district("Palakad", "Keralam")
    assert isolated_osm.call_args.kwargs["params"]["q"] == "Palakkad, Kerala, India"
    osm.geocode_district(" palakkad ", " KERALA ")
    assert isolated_osm.call_count == 1
    osm.geocode_district("Palakkad", "Bihar")
    assert isolated_osm.call_count == 2
    keys = [json.loads(key) for key in osm._load_geocode_cache()]
    assert ["india", "kerala", "palakkad"] in keys
    assert ["india", "bihar", "palakkad"] in keys
    assert normalize_location("Bihar", "Palakad") == ("Bihar", "Palakad")


@pytest.mark.parametrize("raw,canonical", [("Chattisgarh", "Chhattisgarh"),
    ("Pondicherry", "Puducherry"), ("NCT of Delhi", "Delhi")])
def test_documented_state_aliases(raw, canonical):
    assert normalize_location(raw, "Example")[0] == canonical


def test_legacy_cache_and_wrong_state_static_are_not_used(isolated_osm):
    osm._save_geocode_cache({"Aurangabad": {"lat": 19.8, "lon": 75.3}})
    assert osm.geocode_district("Aurangabad", "Bihar")["source"] == "NOT_FOUND"
    assert osm.geocode_district("Aurangabad", "Maharashtra")["source"] == "STATIC_FALLBACK"


@pytest.mark.parametrize("payload", [None, {}, [None], [{"lat": "nan", "lon": "76"}]])
def test_bad_geocode_payload_degrades(payload, isolated_osm):
    isolated_osm.side_effect = lambda *a, **kw: response(payload)
    assert osm.geocode_district("Palakad", "Keralam")["source"] == "NOT_FOUND"


def test_corrupt_and_unwritable_cache_preserves_live_result(monkeypatch, isolated_osm):
    from pathlib import Path
    Path(osm.GEOCODE_CACHE_PATH).write_text("broken", encoding="utf-8")
    isolated_osm.side_effect = lambda *a, **kw: response([{"lat": "10", "lon": "76"}])
    assert osm.geocode_district("Palakad", "Keralam")["source"] == "OSM_LIVE"
    monkeypatch.setattr(osm, "GEOCODE_CACHE_PATH", str(Path(osm.GEOCODE_CACHE_PATH).parent))
    assert osm.geocode_district("Palakad", "Keralam")["source"] == "OSM_LIVE"


def test_discovery_osrm_net_and_raw_provenance(monkeypatch, isolated_osm):
    def http(url, **kwargs):
        if "nominatim" in url:
            return response([{"lat": "10.7", "lon": "76.6"}])
        return response({"code": "Ok", "routes": [{"distance": 100000, "duration": 6000}]})
    isolated_osm.side_effect = http
    records = history("Keralam", "Palakad", "Raw Mandi Name", 2000)
    original = deepcopy(records)
    result = discover(monkeypatch, records, "Karnataka", "Mysore")
    item = result["market_opportunities"][0]
    assert (item["state"], item["district"], item["market"]) == ("Keralam", "Palakad", "Raw Mandi Name")
    assert records == original
    assert item["distance_km"] == 100
    assert item["distance_source"] == "OSRM_LIVE"
    from tools.logistics_core import estimate_transport_cost
    cost = estimate_transport_cost(100, 12, pipeline._load_json("logistics_providers.json"))["cheapest_cost_inr"]
    assert item["gross_market_value"] == 24000
    assert item["estimated_transport_cost"] == cost
    assert item["estimated_net_realization"] == 24000 - cost
    assert item["price_forecast"]["source"] == "POSTGRES / AGMARKNET"
    assert result["data_status_summary"]["distance_data"].startswith("DERIVED")
    assert result["data_status_summary"]["transport_cost"].startswith("SYNTHETIC")
    queries = [call.kwargs["params"]["q"] for call in isolated_osm.call_args_list if "nominatim" in call.args[0]]
    assert queries == ["Mysore, Karnataka, India", "Palakkad, Kerala, India"]


def test_failure_nulls_and_same_district_alias_ranking(monkeypatch, isolated_osm):
    records = history("Bihar", "Aurangabad", "Expensive", 100000)
    records += history("Kerala", "Palakkad", "Local", 100)
    result = discover(monkeypatch, records)
    local, unknown = result["market_opportunities"]
    assert local["market"] == "Local"
    assert local["scope"] == "SAME_DISTRICT"
    assert local["distance_source"] == "SAME_DISTRICT_PROXY"
    assert local["distance_km"] == local["estimated_transport_cost"] == 0
    assert local["estimated_net_realization"] == 1200
    assert unknown["distance_km"] is None
    assert unknown["estimated_transport_cost"] is None
    assert unknown["estimated_net_realization"] is None


def test_same_name_different_states_routes_and_negative_net_ranks_first(monkeypatch, isolated_osm):
    def http(url, **kwargs):
        if "nominatim" in url:
            if "Unknown" in kwargs["params"]["q"]:
                return response([])
            return response([{"lat": "19", "lon": "75"}])
        return response({"code": "Ok", "routes": [{"distance": 2000000, "duration": 60000}]})
    isolated_osm.side_effect = http
    records = history("Bihar", "Aurangabad", "Known", 1)
    records += history("Bihar", "Unknown", "High price", 100000)
    known, unknown = discover(monkeypatch, records, "Maharashtra", "Aurangabad")["market_opportunities"]
    assert known["scope"] == "OTHER_STATE"
    assert known["distance_km"] == 2000
    assert known["estimated_net_realization"] < 0
    assert unknown["estimated_net_realization"] is None


def test_osrm_failure_retains_geocoded_proxy(monkeypatch, isolated_osm):
    def http(url, **kwargs):
        if "nominatim" in url:
            lat = "10" if "Kerala" in kwargs["params"]["q"] else "12"
            return response([{"lat": lat, "lon": "76"}])
        raise requests.Timeout("offline")
    isolated_osm.side_effect = http
    item = discover(monkeypatch, history("Karnataka", "Mysore", "Mandi", 2000))["market_opportunities"][0]
    assert item["distance_source"] == "HAVERSINE_FALLBACK"
    assert item["distance_km"] > 0
    assert item["estimated_net_realization"] is not None


def test_endpoint_preserves_raw_names_and_distance_method(monkeypatch, isolated_osm):
    from fastapi.testclient import TestClient
    import api_v2
    records = history("Keralam", "Palakad", "Raw Mandi", 2000)
    monkeypatch.setattr("db.repositories.get_market_opportunities", lambda *args: records)
    monkeypatch.setattr(api_v2, "_open_db_session", lambda: Mock())
    result = TestClient(api_v2.app).post("/agents/market-discovery", json={
        "crop": "Onion", "farmer_state": "Kerala", "farmer_district": "Palakkad",
        "quantity_quintals": 12, "limit": 10,
    })
    assert result.status_code == 200
    item = result.json()["market_opportunities"][0]
    assert (item["state"], item["district"], item["market"]) == ("Keralam", "Palakad", "Raw Mandi")
    assert item["distance_source"] == "SAME_DISTRICT_PROXY"
    assert item["price_forecast"]["source"] == "POSTGRES / AGMARKNET"
    isolated_osm.assert_not_called()


def test_multiple_mandis_share_one_district_route(monkeypatch):
    from tools.adapters.osm_adapter import OSMRoutingAdapter
    route = Mock(return_value=[{"distance_km": 100, "distance_source": "OSRM_LIVE"}])
    monkeypatch.setattr(OSMRoutingAdapter, "fetch", route)
    records = history("Karnataka", "Mysore", "First", 2000)
    records += history("Karnataka", "Mysore", "Second", 2100)
    result = discover(monkeypatch, records)
    assert result["markets_considered"] == 2
    route.assert_called_once_with("Palakad", "Mysore", origin_state="Keralam", destination_state="Karnataka")

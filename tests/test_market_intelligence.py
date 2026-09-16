"""Mission 2 regressions: disposable SQLite and mocked HTTP only."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from db.models import Commodity, Market, MarketPrice
from db.repositories import get_market_history
from tools import market_intelligence as intelligence
from tools import osm_routing_tool as osm


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(osm, "GEOCODE_CACHE_PATH", str(tmp_path / "coords.json"))
    monkeypatch.setattr(osm, "_respect_rate_limit", lambda: None)
    http = Mock(side_effect=requests.Timeout("offline"))
    monkeypatch.setattr(osm.requests, "get", http)
    return http


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    for table in (Commodity.__table__, Market.__table__, MarketPrice.__table__):
        table.create(engine)
    with Session(engine) as session:
        session.add_all([Commodity(id=1, name="Onion"), Commodity(id=2, name="Tomato"),
                        Market(id=1, name="Raw Mandi", state="Keralam", district="Palakad"),
                        Market(id=2, name="Other Mandi", state="Bihar", district="Unknown")])
        session.flush()
        yield session
    engine.dispose()


def insert(session, id, days=0, market=1, crop=1, source="AGMARKNET", variety="Local", price=2000, unit="quintal"):
    session.add(MarketPrice(id=id, market_id=market, commodity_id=crop,
        arrival_date=date.today() - timedelta(days=days), variety=variety, grade="A", unit=unit,
        min_price=price, modal_price=price, max_price=price, source=source))
    session.flush()


def req(**changes):
    return SimpleNamespace(**{**dict(market_id="1", commodity_id="1", observation_id="3",
        farmer_state="Kerala", farmer_district="Palakkad", quantity_quintals=12), **changes})


def test_real_history_scoped_chronological_and_gaps_not_filled(session):
    insert(session, 1, days=5)
    insert(session, 2, days=2, variety="Other")
    insert(session, 3)
    insert(session, 4, market=2)
    insert(session, 5, crop=2)
    insert(session, 6, source="SYNTHETIC")
    result = get_market_history(session, 1, 1, 3)
    rows = result["observations"]
    assert [row["observation_id"] for row in rows] == ["1", "2", "3"]
    assert [row["observation_date"] for row in rows] == sorted(row["observation_date"] for row in rows)
    assert all(row["market_id"] == row["commodity_id"] == "1" for row in rows)
    assert all(row["provenance"] == {"category": "REAL", "source": "AGMARKNET"} for row in rows)
    assert rows[1]["variety"] == "Other"
    assert get_market_history(session, 1, 1, 4) is None
    assert get_market_history(session, 1, 1, 5) is None
    assert get_market_history(session, 1, 1, 6) is None


@pytest.mark.parametrize("count", [1, 2])
def test_sparse_history_details_keep_selected_observation_and_economics(session, count, offline):
    from api_v2 import MarketDetailsResponse
    for id in range(1, count + 1):
        insert(session, id, days=count-id)
    result = MarketDetailsResponse(**intelligence.build_market_details(session, req(observation_id=str(count)))).model_dump(mode="json")
    assert result["history"]["status"] == "insufficient_history"
    assert result["selected_observation"]["observation_id"] == str(count)
    assert result["selected_observation"]["state"] == "Keralam"
    assert result["economics"]["estimated_net_realization"] == 24000
    assert result["decision"]["recommended_action"] == "INSUFFICIENT_DATA"
    assert result["forecast"]["insufficient_data"] is True
    offline.assert_not_called()


def test_bounded_history_and_selected_row_outside_window(session):
    for id in range(1, 183):
        insert(session, id, days=183-id)
    history = get_market_history(session, 1, 1, 1)
    assert len(history["observations"]) == 180
    assert history["truncated"] is True
    assert history["selected_observation"]["observation_id"] == "1"
    result = intelligence.build_market_details(session, req(observation_id="1"))
    assert result["decision"]["recommended_action"] == "INSUFFICIENT_DATA"


def test_null_economics_and_no_network_for_coordinate_enrichment(session, offline):
    insert(session, 3, market=2)
    result = intelligence.build_market_details(session, req(market_id="2"))
    assert result["economics"]["distance_km"] is None
    assert result["economics"]["estimated_transport_cost"] is None
    assert result["economics"]["estimated_net_realization"] is None
    assert result["coordinates"] == {"latitude": None, "longitude": None, "coordinate_source": None, "coordinate_precision": "unknown"}
    assert offline.call_count == 1  # Failed origin, no coordinate-enrichment request.


def test_coordinate_precision_state_aliases_and_cache_only(offline):
    key = json.dumps(["india", "kerala", "palakkad"])
    osm._save_geocode_cache({key: {"lat": 10.7, "lon": 76.6}})
    result = intelligence.market_coordinates("Keralam", "Palakad")
    assert result["latitude"] == 10.7
    assert result["coordinate_precision"] == "district_centroid"
    assert result["coordinate_source"] == "OSM_GEOCODE_CACHE"
    assert intelligence.market_coordinates("Bihar", "Palakad")["latitude"] is None
    assert intelligence.market_coordinates("Maharashtra", "Pune")["coordinate_source"] == "STATIC_DISTRICT_COORDINATES"
    offline.assert_not_called()


def observations(prices=(100, 110, 120)):
    return [dict(observation_id=str(i), observation_date=f"2026-09-{i+1:02d}",
        modal_price=price, variety="Local", grade="A", unit="quintal") for i, price in enumerate(prices)]


@pytest.mark.parametrize("prices,expected", [((100, 110, 120), "WAIT"), ((120, 110, 100), "SELL_NOW"), ((100, 100, 100), "SELL_NOW")])
def test_deterministic_backend_decision_no_store(prices, expected):
    rows = observations(prices)
    args = (rows[-1], rows, {"estimated_net_realization": 1000}, date(2026, 9, 3))
    first = intelligence.decision_support(*args)
    assert first == intelligence.decision_support(*args)
    assert first[0]["recommended_action"] == expected
    assert "STORE is unavailable" in " ".join(first[0]["limitations"])
    assert first[1]["data_status"] == "DERIVED"


@pytest.mark.parametrize("problem", ["stale", "null_net", "negative_net", "mixed", "duplicates", "gap", "newer"])
def test_decision_abstains_without_valid_support(problem):
    rows = observations()
    selected = rows[-1]
    today = date(2026, 9, 3)
    net = 1000
    if problem == "stale": today = date(2026, 9, 20)
    if problem == "null_net": net = None
    if problem == "negative_net": net = -1
    if problem == "mixed": rows[0]["variety"] = "Other"
    if problem == "duplicates": rows[0]["observation_date"] = rows[1]["observation_date"]
    if problem == "gap": rows[0]["observation_date"] = "2026-08-01"
    if problem == "newer": rows.append({**selected, "observation_id": "9", "observation_date": "2026-09-04"})
    result, _ = intelligence.decision_support(selected, rows, {"estimated_net_realization": net}, today)
    assert result["recommended_action"] == "INSUFFICIENT_DATA"


def test_endpoint_validates_ids_and_hides_database_errors(monkeypatch):
    from fastapi.testclient import TestClient
    import api_v2
    client = TestClient(api_v2.app)
    payload = vars(req())
    assert client.post("/agents/market-details", json={**payload, "market_id": "9223372036854775808"}).status_code == 422
    assert client.post("/agents/market-details", json={**payload, "quantity_quintals": 0}).status_code == 422
    monkeypatch.setattr(api_v2, "_open_db_session", Mock(side_effect=RuntimeError("secret-test-value")))
    response = client.post("/agents/market-details", json=payload)
    assert response.status_code == 503
    assert "secret-test-value" not in response.text


def test_selected_unit_is_not_silently_converted(session):
    insert(session, 3, unit="kg")
    result = intelligence.build_market_details(session, req())
    assert result["selected_observation"]["unit"] == "kg"
    assert result["economics"]["gross_market_value"] is None
    assert result["economics"]["estimated_net_realization"] is None


def test_endpoint_serializes_real_selected_details_and_404(session, monkeypatch):
    from fastapi.testclient import TestClient
    import api_v2
    insert(session, 9007199254740993)
    selected_req = req(observation_id="9007199254740993")
    result = intelligence.build_market_details(session, selected_req)
    fake_session = Mock()
    monkeypatch.setattr(api_v2, "_open_db_session", lambda: fake_session)
    monkeypatch.setattr(intelligence, "build_market_details", lambda *args: result)
    response = TestClient(api_v2.app).post("/agents/market-details", json=vars(selected_req))
    assert response.status_code == 200
    body = response.json()
    assert body["selected_observation"]["observation_id"] == "9007199254740993"
    assert body["history"]["observations"][0]["observation_id"] == "9007199254740993"
    assert body["decision"]["recommended_action"] == "INSUFFICIENT_DATA"
    fake_session.close.assert_called_once()
    monkeypatch.setattr(intelligence, "build_market_details", lambda *args: None)
    assert TestClient(api_v2.app).post("/agents/market-details", json=vars(selected_req)).status_code == 404

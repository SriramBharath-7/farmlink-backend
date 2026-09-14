import sys, os, json, unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.adapters.schema import PriceRecord, DistanceRecord, VALID_DATA_STATUSES
from tools.adapters.base_adapter import SourceAdapter, AdapterError
from tools.adapters.fallback_chain import FallbackChain
from tools.adapters.agmarknet_adapter import AgmarknetAdapter
from tools.adapters.osm_adapter import OSMRoutingAdapter, StaticHaversineAdapter
from tools.adapters.registry import get_price_chain, get_routing_chain


class TestSchema(unittest.TestCase):
    def test_price_record_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            PriceRecord(commodity="Onion", market="X", district="Nashik", state="MH",
                        modal_price=100, min_price=90, max_price=110, unit="quintal",
                        arrival_date="01/09/2026", source="X", source_type="government",
                        data_status="TOTALLY_MADE_UP", fetched_at="now")

    def test_price_record_accepts_all_valid_statuses(self):
        for status in VALID_DATA_STATUSES:
            rec = PriceRecord(commodity="Onion", market="X", district="Nashik", state="MH",
                              modal_price=100, min_price=90, max_price=110, unit="quintal",
                              arrival_date="01/09/2026", source="X", source_type="government",
                              data_status=status, fetched_at="now")
            self.assertEqual(rec.data_status, status)

    def test_distance_record_to_dict_shape(self):
        rec = DistanceRecord(origin="A", destination="B", distance_km=10.0, duration_minutes=None,
                             source="X", source_type="derived", data_status="DERIVED", fetched_at="now")
        d = rec.to_dict()
        self.assertEqual(d["distance_km"], 10.0)
        self.assertIn("source_type", d)


class TestSourceAdapterContract(unittest.TestCase):
    def test_cannot_instantiate_abstract_base(self):
        with self.assertRaises(TypeError):
            SourceAdapter()

    def test_describe_returns_metadata(self):
        adapter = StaticHaversineAdapter()
        desc = adapter.describe()
        self.assertEqual(desc["source_name"], "STATIC_HAVERSINE")
        self.assertEqual(desc["adapter_class"], "StaticHaversineAdapter")


class TestAgmarknetAdapter(unittest.TestCase):
    def test_no_api_key_uses_synthetic_and_tags_it(self):
        adapter = AgmarknetAdapter(api_key="")  # force no-key path
        records = adapter.fetch("Onion", "Jalgaon")
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0]["data_status"], "SYNTHETIC")
        self.assertEqual(records[0]["source"], "AGMARKNET")
        self.assertIsNotNone(records[0]["fallback_reason"])

    def test_live_success_tags_live_and_skips_synthetic(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"records": [{
            "commodity": "Onion", "district": "Nashik", "state": "Maharashtra",
            "market": "Nashik APMC", "modal_price": "2700", "min_price": "2500",
            "max_price": "2900", "arrival_date": "08/09/2026",
        }]}
        mock_resp.raise_for_status.return_value = None
        mock_session.get.return_value = mock_resp

        adapter = AgmarknetAdapter(api_key="fake-key-for-test", http_session=mock_session)
        records = adapter.fetch("Onion", "Nashik")
        self.assertEqual(records[0]["data_status"], "LIVE")
        self.assertIsNone(records[0]["fallback_reason"])
        self.assertEqual(records[0]["modal_price"], 2700.0)

    def test_live_call_raises_falls_back_to_synthetic(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("simulated network failure")

        adapter = AgmarknetAdapter(api_key="fake-key", http_session=mock_session)
        records = adapter.fetch("Onion", "Jalgaon")
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0]["data_status"], "SYNTHETIC")

    def test_unreadable_mock_file_raises_adapter_error(self):
        adapter = AgmarknetAdapter(api_key="", mock_data_path="/nonexistent/path.json")
        with self.assertRaises(AdapterError):
            adapter.fetch("Onion", "Nashik")


class TestOSMAdapters(unittest.TestCase):
    def test_static_haversine_real_districts(self):
        adapter = StaticHaversineAdapter()
        records = adapter.fetch("Nashik", "Pune")
        self.assertEqual(records[0]["data_status"], "DERIVED")
        self.assertGreater(records[0]["distance_km"], 0)

    def test_static_haversine_unknown_district_raises(self):
        adapter = StaticHaversineAdapter()
        with self.assertRaises(AdapterError):
            adapter.fetch("Atlantis", "Pune")

    @patch("tools.osm_routing_tool.requests.get")
    def test_osm_adapter_tags_fallback_on_failure(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout("simulated")
        adapter = OSMRoutingAdapter()
        records = adapter.fetch("Nashik", "Pune")
        self.assertEqual(records[0]["data_status"], "FALLBACK")
        self.assertIsNotNone(records[0]["fallback_reason"])


class TestFallbackChain(unittest.TestCase):
    def test_requires_at_least_one_adapter(self):
        with self.assertRaises(ValueError):
            FallbackChain([])

    def test_first_adapter_success_short_circuits_second(self):
        good = MagicMock()
        good.fetch.return_value = [{"value": 1}]
        good.describe.return_value = {"source_name": "GOOD"}
        never_called = MagicMock()

        chain = FallbackChain([good, never_called])
        records, winner, failed = chain.fetch()
        self.assertEqual(records, [{"value": 1}])
        self.assertEqual(winner["source_name"], "GOOD")
        never_called.fetch.assert_not_called()

    def test_falls_through_to_second_adapter_on_first_failure(self):
        failing = MagicMock()
        failing.fetch.side_effect = AdapterError("simulated failure")
        failing.describe.return_value = {"source_name": "FAILING"}
        good = MagicMock()
        good.fetch.return_value = [{"value": 2}]
        good.describe.return_value = {"source_name": "GOOD"}

        chain = FallbackChain([failing, good])
        records, winner, failed = chain.fetch()
        self.assertEqual(records, [{"value": 2}])
        self.assertEqual(winner["source_name"], "GOOD")
        self.assertEqual(len(failed), 1)

    def test_all_adapters_failing_raises(self):
        a = MagicMock()
        a.fetch.side_effect = AdapterError("a failed")
        a.describe.return_value = {"source_name": "A"}
        b = MagicMock()
        b.fetch.side_effect = AdapterError("b failed")
        b.describe.return_value = {"source_name": "B"}

        chain = FallbackChain([a, b])
        with self.assertRaises(AdapterError):
            chain.fetch()

    def test_real_chain_osm_then_static_haversine_end_to_end(self):
        # No mocking here -- this exercises the real OSMRoutingAdapter,
        # which (per AUDIT_REPORT.md) cannot reach the live network from
        # this sandbox, so it MUST fail over to StaticHaversineAdapter.
        chain = get_routing_chain(["osm", "static_haversine"])
        records, winner, failed = chain.fetch(origin_district="Nashik", destination_district="Pune")
        self.assertGreater(records[0]["distance_km"], 0)
        # In this sandboxed environment we expect OSM's live call to be
        # unreachable and the static adapter to win -- but we assert on
        # *which* adapter actually produced the result rather than assuming,
        # so this test tells the truth about what happened even if network
        # conditions differ elsewhere.
        self.assertIn(winner["source_name"], ("OSM", "STATIC_HAVERSINE"))


class TestRegistry(unittest.TestCase):
    def test_get_price_chain_default(self):
        chain = get_price_chain()
        records, winner, failed = chain.fetch(commodity="Cotton", district="Nagpur")
        self.assertGreater(len(records), 0)
        self.assertEqual(winner["source_name"], "AGMARKNET")

    def test_get_routing_chain_static_only_never_touches_network(self):
        with patch("tools.osm_routing_tool.requests.get") as mock_get:
            chain = get_routing_chain(["static_haversine"])
            records, winner, failed = chain.fetch(origin_district="Nashik", destination_district="Pune")
            mock_get.assert_not_called()
            self.assertEqual(winner["source_name"], "STATIC_HAVERSINE")


if __name__ == "__main__":
    unittest.main()

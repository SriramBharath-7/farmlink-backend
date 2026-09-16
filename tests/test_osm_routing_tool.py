import sys, os, json, unittest
import tempfile
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.osm_routing_tool as osm

CACHE_PATH = osm.GEOCODE_CACHE_PATH

# The rate limiter is correct and necessary at runtime (both Nominatim and
# OSRM cap external users at 1 req/sec), but it has no place slowing down
# a mocked unit test suite -- patch it out for every test in this module.
_sleep_patcher = patch("tools.osm_routing_tool.time.sleep", return_value=None)


def setUpModule():
    global CACHE_PATH, _cache_dir, _cache_patcher
    _cache_dir = tempfile.TemporaryDirectory()
    CACHE_PATH = os.path.join(_cache_dir.name, "cache.json")
    _cache_patcher = patch.object(osm, "GEOCODE_CACHE_PATH", CACHE_PATH)
    _cache_patcher.start()
    _sleep_patcher.start()


def tearDownModule():
    _sleep_patcher.stop()
    _cache_patcher.stop()
    _cache_dir.cleanup()


def _clear_cache_file():
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)


class TestGeocodeDistrict(unittest.TestCase):
    def setUp(self):
        _clear_cache_file()
        osm._last_call_ts = 0.0  # reset rate limiter between tests

    def tearDown(self):
        _clear_cache_file()

    @patch("tools.osm_routing_tool.requests.get")
    def test_successful_geocode_real_schema_shape(self, mock_get):
        # Response shape matches Nominatim's documented /search JSON output
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"lat": "19.9975000", "lon": "73.7898000", "display_name": "Nashik, Maharashtra, India"}
        ]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = osm.geocode_district("Nashik", use_cache=False)
        self.assertEqual(result["source"], "OSM_LIVE")
        self.assertAlmostEqual(result["lat"], 19.9975, places=3)
        self.assertAlmostEqual(result["lon"], 73.7898, places=3)

    @patch("tools.osm_routing_tool.requests.get")
    def test_sends_required_user_agent_header(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"lat": "19.0", "lon": "73.0"}]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        osm.geocode_district("Pune", use_cache=False)
        _, kwargs = mock_get.call_args
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertTrue(len(kwargs["headers"]["User-Agent"]) > 0)

    @patch("tools.osm_routing_tool.requests.get")
    def test_network_failure_falls_back_to_static_table(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout("simulated timeout")

        result = osm.geocode_district("Nashik", use_cache=False)
        self.assertEqual(result["source"], "STATIC_FALLBACK")
        self.assertIsNotNone(result["lat"])  # Nashik IS in the static table

    @patch("tools.osm_routing_tool.requests.get")
    def test_empty_results_falls_back(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []  # Nominatim returns [] when nothing matches
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = osm.geocode_district("Nashik", use_cache=False)
        self.assertEqual(result["source"], "STATIC_FALLBACK")

    def test_unknown_district_not_in_static_table_returns_not_found(self):
        with patch("tools.osm_routing_tool.requests.get") as mock_get:
            import requests
            mock_get.side_effect = requests.exceptions.ConnectionError("no network")
            result = osm.geocode_district("Atlantis", use_cache=False)
            self.assertEqual(result["source"], "NOT_FOUND")
            self.assertIsNone(result["lat"])

    @patch("tools.osm_routing_tool.requests.get")
    def test_cache_prevents_second_live_call(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"lat": "19.9975", "lon": "73.7898"}]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        osm.geocode_district("Nashik", use_cache=True)
        self.assertEqual(mock_get.call_count, 1)
        osm.geocode_district("Nashik", use_cache=True)
        self.assertEqual(mock_get.call_count, 1)  # second call served from disk cache


class TestGetRoadRoute(unittest.TestCase):
    def setUp(self):
        osm._last_call_ts = 0.0

    @patch("tools.osm_routing_tool.requests.get")
    def test_successful_route_real_schema_shape(self, mock_get):
        # Response shape matches OSRM's documented /route/v1 JSON output
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": "Ok",
            "routes": [{"distance": 220400.0, "duration": 14520.0}],  # meters, seconds
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = osm.get_road_route((19.9975, 73.7898), (18.5204, 73.8567))
        self.assertEqual(result["source"], "OSRM_LIVE")
        self.assertEqual(result["distance_km"], 220.4)
        self.assertEqual(result["duration_minutes"], 242.0)

    @patch("tools.osm_routing_tool.requests.get")
    def test_lon_lat_order_sent_to_osrm(self, mock_get):
        # OSRM requires lon,lat order (opposite of lat,lon) -- easy to get backwards
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": "Ok", "routes": [{"distance": 1000, "duration": 60}]}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        osm.get_road_route((19.9975, 73.7898), (18.5204, 73.8567))
        called_url = mock_get.call_args[0][0]
        # lon (73.7898) must appear before lat (19.9975) in the URL path
        self.assertIn("73.7898,19.9975", called_url)

    @patch("tools.osm_routing_tool.requests.get")
    def test_osrm_no_route_falls_back_to_haversine(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": "NoRoute", "routes": []}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = osm.get_road_route((19.9975, 73.7898), (18.5204, 73.8567))
        self.assertEqual(result["source"], "HAVERSINE_FALLBACK")
        self.assertIsNotNone(result["distance_km"])
        self.assertIsNone(result["duration_minutes"])

    @patch("tools.osm_routing_tool.requests.get")
    def test_osrm_timeout_falls_back_to_haversine(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout("simulated")

        result = osm.get_road_route((19.9975, 73.7898), (18.5204, 73.8567))
        self.assertEqual(result["source"], "HAVERSINE_FALLBACK")
        # sanity: fallback distance should roughly match the tested haversine x1.25
        from tools.district_geo import haversine_km
        expected = round(haversine_km((19.9975, 73.7898), (18.5204, 73.8567)) * 1.25, 1)
        self.assertEqual(result["distance_km"], expected)

    def test_none_coordinates_short_circuits_without_network_call(self):
        with patch("tools.osm_routing_tool.requests.get") as mock_get:
            result = osm.get_road_route((None, None), (18.5, 73.8))
            self.assertEqual(result["source"], "UNRESOLVED_COORDINATES")
            mock_get.assert_not_called()


class TestEstimateDistrictToDistrict(unittest.TestCase):
    def setUp(self):
        _clear_cache_file()
        osm._last_call_ts = 0.0

    def tearDown(self):
        _clear_cache_file()

    @patch("tools.osm_routing_tool.requests.get")
    def test_full_pipeline_with_all_live_calls_mocked(self, mock_get):
        def side_effect(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "nominatim" in url:
                resp.json.return_value = [{"lat": "19.9975", "lon": "73.7898"}]
            else:
                resp.json.return_value = {"code": "Ok", "routes": [{"distance": 100000, "duration": 6000}]}
            return resp
        mock_get.side_effect = side_effect

        result = osm.estimate_district_to_district("Nashik", "Pune")
        self.assertNotIn("error", result)
        self.assertEqual(result["distance_km"], 100.0)
        self.assertEqual(result["distance_source"], "OSRM_LIVE")

    def test_unresolvable_district_returns_error(self):
        with patch("tools.osm_routing_tool.requests.get") as mock_get:
            import requests
            mock_get.side_effect = requests.exceptions.ConnectionError("no network")
            result = osm.estimate_district_to_district("Atlantis", "Pune")
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()

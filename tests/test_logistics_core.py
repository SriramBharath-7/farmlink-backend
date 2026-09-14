import sys, os, json, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.logistics_core import estimate_distance, estimate_transport_cost, lookup_storage

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load(name):
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


class TestLogisticsCore(unittest.TestCase):
    def test_known_districts_returns_distance(self):
        out = estimate_distance("Nashik", "Pune")
        self.assertNotIn("error", out)
        self.assertGreater(out["straight_line_km"], 0)
        self.assertGreater(out["estimated_road_km"], out["straight_line_km"])

    def test_road_estimate_is_125x_straight_line(self):
        out = estimate_distance("Nashik", "Pune")
        self.assertAlmostEqual(out["estimated_road_km"], round(out["straight_line_km"] * 1.25, 1))

    def test_unknown_district_returns_error_not_crash(self):
        out = estimate_distance("Nashik", "Atlantis")
        self.assertIn("error", out)

    def test_same_district_zero_distance(self):
        out = estimate_distance("Pune", "Pune")
        self.assertEqual(out["straight_line_km"], 0.0)

    def test_transport_cost_against_real_provider_data(self):
        providers = load("logistics_providers.json")
        out = estimate_transport_cost(distance_km=100, quantity_quintals=20, providers=providers)
        self.assertNotIn("error", out)
        self.assertLessEqual(len(out["quotes"]), 5)
        costs = [q["estimated_total_cost_inr"] for q in out["quotes"]]
        self.assertEqual(costs, sorted(costs))  # sorted cheapest-first
        # sanity check against the actual cheapest provider's rate in the real file
        cheapest_rate = min(p["rate_per_km_per_quintal"] for p in providers)
        self.assertAlmostEqual(out["cheapest_cost_inr"], round(cheapest_rate * 100 * 20, 2))

    def test_transport_cost_invalid_inputs(self):
        providers = load("logistics_providers.json")
        self.assertIn("error", estimate_transport_cost(-5, 20, providers))
        self.assertIn("error", estimate_transport_cost(100, 0, providers))
        self.assertIn("error", estimate_transport_cost(None, 20, providers))

    def test_storage_lookup_real_data_nashik_has_facility(self):
        facilities = load("cold_storage.json")
        out = lookup_storage("Nashik", facilities, crop="Grapes")
        self.assertFalse(out["widened_search_statewide"])
        self.assertTrue(any(f["district"] == "Nashik" for f in out["facilities"]))

    def test_storage_lookup_widens_when_nothing_local(self):
        facilities = load("cold_storage.json")
        # "Mumbai" has no cold storage entries in the real dataset at all
        out = lookup_storage("Mumbai", facilities)
        self.assertTrue(out["widened_search_statewide"])
        self.assertGreater(len(out["facilities"]), 0)


if __name__ == "__main__":
    unittest.main()

import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.decision_pipeline import build_matching_result, build_logistics_result, build_sell_decision


class TestBuildMatchingResult(unittest.TestCase):
    def test_shape_and_consistency_with_full_decision(self):
        matching = build_matching_result("Onion", "Nashik", 20, "A")
        full = build_sell_decision("Onion", "Nashik", 20, "A")
        self.assertEqual(matching["buyer_shortlist"], full["buyer_shortlist"])
        self.assertEqual(matching["reference_mandi_price"], full["price_forecast"]["current_modal"])

    def test_unknown_commodity_still_returns_structure_not_crash(self):
        result = build_matching_result("Dragonfruit", "Nashik", 20, "A")
        # no price data exists for a nonexistent crop -> pipeline must
        # report unavailability explicitly, never crash and never
        # fabricate a buyer shortlist against a price that doesn't exist
        self.assertIn("error", result)
        self.assertEqual(result["data_status"], "UNAVAILABLE")

    def test_ranked_by_match_score_descending_among_eligible(self):
        result = build_matching_result("Cotton", "Nagpur", 30, "B")
        scores = [b["match_score"] for b in result["buyer_shortlist"] if b["meets_grade_requirement"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestBuildLogisticsResult(unittest.TestCase):
    def test_shape_real_districts(self):
        result = build_logistics_result("Nashik", "Pune", 20)
        self.assertNotIn("error", result)
        self.assertGreater(result["distance_km"], 0)
        self.assertGreater(len(result["transport_quotes"]), 0)
        self.assertEqual(result["distance_source"], "HAVERSINE_PROXY")

    def test_transport_quotes_sorted_cheapest_first(self):
        result = build_logistics_result("Nashik", "Pune", 20)
        costs = [q["estimated_total_cost_inr"] for q in result["transport_quotes"]]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(result["cheapest_transport_cost_inr"], costs[0])

    def test_unknown_district_returns_error_not_crash(self):
        result = build_logistics_result("Atlantis", "Pune", 20)
        self.assertIn("error", result)

    def test_storage_alternative_present(self):
        result = build_logistics_result("Nashik", "Pune", 20)
        self.assertIn("facilities", result["storage_alternative"])


if __name__ == "__main__":
    unittest.main()

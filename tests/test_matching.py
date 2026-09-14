import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.matching_engine import calculate_match_score, rank_matched_buyers, WEIGHTS


def make_buyer(**kwargs):
    base = {
        "buyer_id": "B0001",
        "name": "Test Buyer",
        "district": "Nashik",
        "commodities_wanted": ["Onion"],
        "typical_volume_quintals": 100,
        "reliability_score": 4.0,
        "target_price_index": 1.0,
        "minimum_grade_accepted": "B",
    }
    base.update(kwargs)
    return base


class TestMatchingEngine(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)

    def test_grade_below_minimum_fails_quality(self):
        buyer = make_buyer(minimum_grade_accepted="A")
        result = calculate_match_score(
            buyer=buyer, commodity="Onion", farmer_grade="C", quantity_quintals=20,
            distance_km=50, transport_cost=1000, all_candidate_offer_prices=[2700],
            mandi_modal_price=2700,
        )
        self.assertFalse(result["meets_grade_requirement"])
        self.assertEqual(result["score_breakdown"]["quality_score"], 0.0)

    def test_grade_meets_minimum_passes(self):
        buyer = make_buyer(minimum_grade_accepted="B")
        result = calculate_match_score(
            buyer=buyer, commodity="Onion", farmer_grade="A", quantity_quintals=20,
            distance_km=50, transport_cost=1000, all_candidate_offer_prices=[2700],
            mandi_modal_price=2700,
        )
        self.assertTrue(result["meets_grade_requirement"])
        self.assertGreater(result["score_breakdown"]["quality_score"], 0)

    def test_wrong_commodity_zero_demand_fit(self):
        buyer = make_buyer(commodities_wanted=["Cotton"])
        result = calculate_match_score(
            buyer=buyer, commodity="Onion", farmer_grade="A", quantity_quintals=20,
            distance_km=50, transport_cost=1000, all_candidate_offer_prices=[2700],
            mandi_modal_price=2700,
        )
        self.assertEqual(result["score_breakdown"]["demand_fit_score"], 0.0)

    def test_distance_score_decreases_with_distance(self):
        buyer = make_buyer()
        near = calculate_match_score(
            buyer=buyer, commodity="Onion", farmer_grade="A", quantity_quintals=20,
            distance_km=10, transport_cost=500, all_candidate_offer_prices=[2700],
            mandi_modal_price=2700,
        )
        far = calculate_match_score(
            buyer=buyer, commodity="Onion", farmer_grade="A", quantity_quintals=20,
            distance_km=400, transport_cost=8000, all_candidate_offer_prices=[2700],
            mandi_modal_price=2700,
        )
        self.assertGreater(near["score_breakdown"]["distance_score"], far["score_breakdown"]["distance_score"])
        self.assertGreater(near["match_score"], far["match_score"])

    def test_distance_none_scores_zero_not_crash(self):
        buyer = make_buyer()
        result = calculate_match_score(
            buyer=buyer, commodity="Onion", farmer_grade="A", quantity_quintals=20,
            distance_km=None, transport_cost=0, all_candidate_offer_prices=[2700],
            mandi_modal_price=2700,
        )
        self.assertEqual(result["score_breakdown"]["distance_score"], 0.0)

    def test_ranking_is_deterministic_and_grade_failures_sink(self):
        buyers = [
            make_buyer(buyer_id="B1", minimum_grade_accepted="A", target_price_index=1.10, reliability_score=3.0),
            make_buyer(buyer_id="B2", minimum_grade_accepted="C", target_price_index=0.95, reliability_score=4.9),
        ]

        def fake_lookup(district):
            return (50.0, 1500.0)

        ranked = rank_matched_buyers(
            candidate_buyers=buyers, commodity="Onion", farmer_grade="B",
            quantity_quintals=20, mandi_modal_price=2700,
            distance_and_cost_lookup=fake_lookup,
        )
        # B1 requires grade A, farmer only has B -> fails, must not be ranked #1
        # even though it has a higher price_index.
        self.assertEqual(ranked[0]["buyer_id"], "B2")
        self.assertFalse(ranked[1]["meets_grade_requirement"])

    def test_higher_price_buyer_not_always_top_after_distance_penalty(self):
        buyers = [
            make_buyer(buyer_id="FAR_HIGH", district="Gadchiroli", target_price_index=1.15, reliability_score=4.0),
            make_buyer(buyer_id="NEAR_LOW", district="Nashik", target_price_index=1.00, reliability_score=4.0),
        ]

        def fake_lookup(district):
            return (450.0, 9000.0) if district == "Gadchiroli" else (15.0, 300.0)

        ranked = rank_matched_buyers(
            candidate_buyers=buyers, commodity="Onion", farmer_grade="B",
            quantity_quintals=20, mandi_modal_price=2700,
            distance_and_cost_lookup=fake_lookup,
        )
        # both meet grade; verify net_realization for the near buyer is
        # actually higher despite the lower headline price, given transport
        near = next(r for r in ranked if r["buyer_id"] == "NEAR_LOW")
        far = next(r for r in ranked if r["buyer_id"] == "FAR_HIGH")
        self.assertGreater(near["net_realization"], far["net_realization"])


if __name__ == "__main__":
    unittest.main()

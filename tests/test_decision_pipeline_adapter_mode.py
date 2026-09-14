import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.decision_pipeline import build_sell_decision


class TestDecisionPipelineAdapterMode(unittest.TestCase):
    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="not_a_real_mode")

    def test_direct_and_adapter_modes_produce_same_price(self):
        direct = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="direct")
        adapter = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="adapter")
        self.assertEqual(direct["price_forecast"]["current_modal"], adapter["price_forecast"]["current_modal"])
        self.assertEqual(direct["price_forecast"]["trend"], adapter["price_forecast"]["trend"])

    def test_direct_and_adapter_modes_produce_same_recommendation(self):
        direct = build_sell_decision("Cotton", "Nagpur", 30, "B", data_source_mode="direct")
        adapter = build_sell_decision("Cotton", "Nagpur", 30, "B", data_source_mode="adapter")
        self.assertEqual(direct["recommended_action"], adapter["recommended_action"])

    def test_direct_and_adapter_modes_produce_same_top_buyer_and_distance(self):
        direct = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="direct")
        adapter = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="adapter")
        self.assertEqual(direct["buyer_shortlist"][0]["buyer_id"], adapter["buyer_shortlist"][0]["buyer_id"])
        self.assertEqual(direct["buyer_shortlist"][0]["distance_km"], adapter["buyer_shortlist"][0]["distance_km"])
        self.assertEqual(direct["buyer_shortlist"][0]["net_realization"], adapter["buyer_shortlist"][0]["net_realization"])

    def test_adapter_mode_labels_source_correctly(self):
        adapter = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="adapter")
        self.assertEqual(adapter["price_forecast"]["source"], "AGMARKNET")

    def test_direct_mode_unaffected_by_new_parameter_default(self):
        # calling without data_source_mode at all must behave exactly as
        # before this parameter existed (excluding generated_at, which
        # legitimately differs by microseconds between any two calls)
        implicit = build_sell_decision("Onion", "Nashik", 20, "A")
        explicit = build_sell_decision("Onion", "Nashik", 20, "A", data_source_mode="direct")
        implicit.pop("generated_at")
        explicit.pop("generated_at")
        self.assertEqual(implicit, explicit)


if __name__ == "__main__":
    unittest.main()

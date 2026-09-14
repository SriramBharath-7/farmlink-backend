import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.finance_tool import calculate_net_realization, compare_offers


class TestFinance(unittest.TestCase):
    def test_basic_math(self):
        out = calculate_net_realization(
            offer_price_per_quintal=300, quantity_quintals=20,
            transport_cost=2500, storage_cost=0,
        )
        self.assertEqual(out["gross_revenue"], 6000)
        self.assertEqual(out["net_realization"], 3500)
        self.assertEqual(out["net_realization_per_quintal"], 175.0)

    def test_zero_quantity_no_division_error(self):
        out = calculate_net_realization(offer_price_per_quintal=300, quantity_quintals=0)
        self.assertEqual(out["net_realization_per_quintal"], 0.0)

    def test_negative_quantity_raises(self):
        with self.assertRaises(ValueError):
            calculate_net_realization(offer_price_per_quintal=300, quantity_quintals=-5)

    def test_negative_price_raises(self):
        with self.assertRaises(ValueError):
            calculate_net_realization(offer_price_per_quintal=-1, quantity_quintals=5)

    def test_platform_fee_applied(self):
        out = calculate_net_realization(
            offer_price_per_quintal=100, quantity_quintals=10, platform_fee_pct=2.0
        )
        # gross = 1000, fee = 20
        self.assertEqual(out["platform_fee"], 20.0)
        self.assertEqual(out["net_realization"], 980.0)

    def test_higher_headline_price_can_lose_to_lower_price_after_transport(self):
        # This is the exact "Buyer A vs Buyer B" scenario from the blueprint.
        offers = [
            {"label": "Buyer A", "offer_price_per_quintal": 3000, "quantity_quintals": 20, "transport_cost": 8000},
            {"label": "Buyer B", "offer_price_per_quintal": 2900, "quantity_quintals": 20, "transport_cost": 2000},
        ]
        ranked = compare_offers(offers)
        self.assertEqual(ranked[0]["label"], "Buyer B")
        self.assertGreater(ranked[0]["net_realization"], ranked[1]["net_realization"])

    def test_compare_offers_sorted_descending(self):
        offers = [
            {"label": "X", "offer_price_per_quintal": 100, "quantity_quintals": 10, "transport_cost": 0},
            {"label": "Y", "offer_price_per_quintal": 200, "quantity_quintals": 10, "transport_cost": 0},
            {"label": "Z", "offer_price_per_quintal": 150, "quantity_quintals": 10, "transport_cost": 0},
        ]
        ranked = compare_offers(offers)
        nets = [r["net_realization"] for r in ranked]
        self.assertEqual(nets, sorted(nets, reverse=True))


if __name__ == "__main__":
    unittest.main()

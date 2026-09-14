import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.forecast_tool import forecast_price


def make_records(prices, start="01/09/2026"):
    from datetime import datetime, timedelta
    start_dt = datetime.strptime(start, "%d/%m/%Y")
    return [
        {"arrival_date": (start_dt + timedelta(days=i)).strftime("%d/%m/%Y"), "modal_price": p}
        for i, p in enumerate(prices)
    ]


class TestForecast(unittest.TestCase):
    def test_no_records(self):
        out = forecast_price([])
        self.assertTrue(out["insufficient_data"])
        self.assertEqual(out["record_count"], 0)

    def test_too_few_records(self):
        out = forecast_price(make_records([100, 105]))
        self.assertTrue(out["insufficient_data"])
        self.assertEqual(out["record_count"], 2)

    def test_flat_prices_stable_trend(self):
        out = forecast_price(make_records([100] * 10))
        self.assertFalse(out["insufficient_data"])
        self.assertEqual(out["trend"], "STABLE")
        self.assertEqual(out["change_percent_vs_baseline"], 0.0)
        self.assertEqual(out["volatility_pct"], 0.0)
        # zero volatility -> confidence should be at the high end (no data penalty, n=10<14 so small penalty)
        self.assertGreaterEqual(out["confidence"], 0.7)

    def test_upward_trend_detected(self):
        # steadily rising prices well past the 1.5% threshold
        prices = [100, 101, 102, 104, 106, 108, 111, 115, 120, 126]
        out = forecast_price(make_records(prices))
        self.assertEqual(out["trend"], "UPWARD")
        self.assertGreater(out["change_percent_vs_baseline"], 1.5)
        self.assertEqual(out["current_modal"], 126)
        self.assertGreater(out["expected_range"]["max"], out["expected_range"]["min"])

    def test_downward_trend_detected(self):
        prices = [126, 120, 115, 111, 108, 106, 104, 102, 101, 100]
        out = forecast_price(make_records(prices))
        self.assertEqual(out["trend"], "DOWNWARD")
        self.assertLess(out["change_percent_vs_baseline"], -1.5)

    def test_moving_averages_correct(self):
        prices = list(range(1, 21))  # 1..20, ma_7 should be avg of last 7 = 14..20 avg = 17.0
        out = forecast_price(make_records(prices))
        self.assertEqual(out["moving_average_7d"], sum(range(14, 21)) / 7)
        self.assertEqual(out["moving_average_14d"], sum(range(7, 21)) / 14)

    def test_confidence_bounds(self):
        # extremely volatile data should push confidence toward the floor, never below it
        prices = [100, 300, 50, 400, 20, 500, 10, 600, 5, 700]
        out = forecast_price(make_records(prices))
        self.assertGreaterEqual(out["confidence"], 0.30)
        self.assertLessEqual(out["confidence"], 0.90)

    def test_unsorted_input_handled(self):
        recs = make_records([100, 101, 102, 104, 106, 108, 111, 115, 120, 126])
        import random
        shuffled = recs[:]
        random.shuffle(shuffled)
        out_sorted = forecast_price(recs)
        out_shuffled = forecast_price(shuffled)
        self.assertEqual(out_sorted["current_modal"], out_shuffled["current_modal"])
        self.assertEqual(out_sorted["trend"], out_shuffled["trend"])


if __name__ == "__main__":
    unittest.main()

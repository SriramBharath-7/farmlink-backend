import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient
from api_v2 import app

client = TestClient(app)


class TestHealth(unittest.TestCase):
    def test_health(self):
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")


class TestPriceIntelligenceEndpoint(unittest.TestCase):
    def test_real_request_returns_typed_forecast(self):
        r = client.post("/agents/price-intelligence", json={
            "crop": "Cotton", "district": "Nagpur", "quantity_quintals": 20,
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("trend", body)
        self.assertIn("confidence", body)
        self.assertIn("expected_range", body)
        self.assertFalse(body["insufficient_data"])

    def test_nonexistent_commodity_returns_422_not_500(self):
        r = client.post("/agents/price-intelligence", json={
            "crop": "Dragonfruit", "district": "Nashik", "quantity_quintals": 20,
        })
        self.assertEqual(r.status_code, 422)


class TestMatchBuyersEndpoint(unittest.TestCase):
    def test_real_request_returns_ranked_shortlist(self):
        r = client.post("/agents/match-buyers", json={
            "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreater(len(body["buyer_shortlist"]), 0)
        scores = [b["match_score"] for b in body["buyer_shortlist"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # every buyer must carry its full score breakdown, not a summary
        self.assertIn("price_score", body["buyer_shortlist"][0]["score_breakdown"])


class TestLogisticsEndpoint(unittest.TestCase):
    def test_real_request(self):
        r = client.post("/agents/logistics", json={
            "origin_district": "Nashik", "destination_district": "Pune", "quantity_quintals": 20,
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreater(body["distance_km"], 0)
        self.assertGreater(len(body["transport_quotes"]), 0)

    def test_unknown_district_422(self):
        r = client.post("/agents/logistics", json={
            "origin_district": "Atlantis", "destination_district": "Pune", "quantity_quintals": 20,
        })
        self.assertEqual(r.status_code, 422)


class TestGrievanceEndpoint(unittest.TestCase):
    def test_real_request_includes_farmer_message(self):
        r = client.post("/agents/grievance", json={
            "complaint_type": "payment_not_received", "days_since_issue": 4,
            "complaint_text": "Buyer hasn't paid.",
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["escalated"])
        self.assertIn(body["ticket_id"], body["farmer_message"])

    def test_unknown_category_still_200_with_other_fallback(self):
        r = client.post("/agents/grievance", json={
            "complaint_type": "some typo'd thing", "days_since_issue": 1,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["complaint_type"], "other")
        self.assertFalse(r.json()["matched_known_category"])


class TestSellDecisionEndpoint(unittest.TestCase):
    def test_without_llm_explanation(self):
        r = client.post("/agents/sell-decision", json={
            "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
            "include_llm_explanation": False,
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["reasoning_text"])
        self.assertEqual(body["llm_explanation_status"], "not_requested")
        self.assertIn(body["recommended_action"], ("SELL_NOW", "WAIT", "NO_VERIFIED_BUYER_MATCH"))

    def test_llm_explanation_gracefully_degrades_without_crewai_installed(self):
        # This sandbox doesn't have crewai installed -- confirms the
        # endpoint returns the full structured result anyway, with a
        # clear status, rather than a 500.
        r = client.post("/agents/sell-decision", json={
            "crop": "Onion", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
            "include_llm_explanation": True,
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["reasoning_text"])
        self.assertTrue(body["llm_explanation_status"].startswith("unavailable"))
        # the numeric result must still be fully present and correct
        self.assertGreater(len(body["buyer_shortlist"]), 0)
        self.assertIn("current_modal", body["price_forecast"])

    def test_error_case_returns_422_with_detail(self):
        r = client.post("/agents/sell-decision", json={
            "crop": "Dragonfruit", "district": "Nashik", "quantity_quintals": 20, "grade": "A",
        })
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()

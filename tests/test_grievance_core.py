import sys, os, random, unittest
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.grievance_core import evaluate_grievance, generate_ticket_id


class TestGrievanceCore(unittest.TestCase):
    def test_ticket_id_format_deterministic_with_injected_rng(self):
        fixed_now = datetime(2026, 9, 8)
        fixed_rng = random.Random(1)
        tid = generate_ticket_id(now=fixed_now, rng=fixed_rng)
        self.assertTrue(tid.startswith("GRV-20260908-"))
        self.assertEqual(len(tid), len("GRV-20260908-") + 6)

    def test_payment_not_received_escalates_at_threshold(self):
        result = evaluate_grievance("payment_not_received", days_since_issue=3)
        self.assertTrue(result["escalated"])
        self.assertEqual(result["severity"], "high")
        self.assertEqual(result["status"], "escalated_to_admin_review")

    def test_payment_not_received_below_threshold_not_escalated(self):
        result = evaluate_grievance("payment_not_received", days_since_issue=2)
        self.assertFalse(result["escalated"])
        self.assertEqual(result["severity"], "medium")

    def test_never_auto_escalates_category(self):
        result = evaluate_grievance("quality_mismatch", days_since_issue=100)
        self.assertFalse(result["escalated"])
        self.assertEqual(result["severity"], "medium")

    def test_unknown_category_falls_back_to_other(self):
        result = evaluate_grievance("something_made_up", days_since_issue=10)
        self.assertFalse(result["matched_known_category"])
        self.assertEqual(result["complaint_type"], "other")
        self.assertEqual(result["severity"], "low")

    def test_category_normalization_case_and_spaces(self):
        a = evaluate_grievance("Payment Not Received", days_since_issue=3)
        b = evaluate_grievance("payment_not_received", days_since_issue=3)
        self.assertEqual(a["complaint_type"], b["complaint_type"])
        self.assertEqual(a["escalated"], b["escalated"])

    def test_buyer_unresponsive_threshold(self):
        below = evaluate_grievance("buyer_unresponsive", days_since_issue=1)
        at = evaluate_grievance("buyer_unresponsive", days_since_issue=2)
        self.assertFalse(below["escalated"])
        self.assertTrue(at["escalated"])
        self.assertEqual(at["severity"], "high")

    def test_farmer_message_mentions_ticket_and_severity_escalated(self):
        from tools.grievance_core import generate_farmer_message
        result = evaluate_grievance("payment_not_received", days_since_issue=5)
        msg = generate_farmer_message(result)
        self.assertIn(result["ticket_id"], msg)
        self.assertIn("escalated", msg.lower())
        self.assertIn(result["severity"], msg)

    def test_farmer_message_not_escalated_case(self):
        from tools.grievance_core import generate_farmer_message
        result = evaluate_grievance("quality_mismatch", days_since_issue=1)
        msg = generate_farmer_message(result)
        self.assertIn(result["ticket_id"], msg)
        self.assertNotIn("escalated", msg.lower())


if __name__ == "__main__":
    unittest.main()

"""
grievance_rules_tool.py
-------------------------
CrewAI tool for the Grievance Agent: a simple, transparent rules table
that decides escalation/severity. Kept deliberately simple and inspectable
for the demo/pitch -- judges can see exactly why a ticket got its status,
which is itself part of the "trust layer" pitch.

CHANGED vs. the original version of this file:
  1. The actual rule logic now lives in tools/grievance_core.py as a
     plain function with no crewai dependency, unit tested directly
     (9 tests, passing, tools/tests/test_grievance_core.py).
  2. BUGFIX: the original version of this file labeled a ticket with
     the raw, unrecognized complaint_type string even when it had
     silently fallen back to the "other" rule -- e.g. a typo'd category
     would get "other"'s severity/escalation behavior but still display
     as if it were a matched category, which is a misleading audit
     trail. grievance_core.evaluate_grievance() now reports
     complaint_type="other" whenever the "other" rule was actually
     applied, and separately preserves the raw input for debugging.
     Caught by test_unknown_category_falls_back_to_other.
"""

import json
from crewai.tools import tool
from tools.grievance_core import evaluate_grievance


@tool("Grievance Rule Evaluator")
def grievance_rule_evaluator(complaint_type: str, days_since_issue: int = 0) -> str:
    """
    Applies a transparent rules table to a farmer or buyer complaint to
    decide its severity and whether it should be auto-escalated, and
    issues a ticket ID. Valid complaint_type values: payment_not_received,
    quality_mismatch, quantity_mismatch, buyer_unresponsive,
    logistics_delay, other. Any other value is treated as "other" and
    the response will say so explicitly (matched_known_category: false).

    Args:
        complaint_type: One of the categories listed above.
        days_since_issue: Days elapsed since the deal/issue occurred.

    Returns:
        A JSON string with ticket_id, complaint_type (normalized -- will
        read "other" if the input wasn't a recognized category, even if
        your raw input string differs), matched_known_category, severity,
        escalated (bool), and the rule that was applied (for transparency).
    """
    return json.dumps(evaluate_grievance(complaint_type, days_since_issue), ensure_ascii=False)

"""
grievance_core.py
--------------------
Pure extraction of grievance_rules_tool.py's logic, same rationale as
logistics_core.py -- testable without crewai, reusable by
decision_pipeline-style callers, unchanged behavior for the existing
agent.
"""

import random
import string
from datetime import datetime
from typing import Optional, Dict

COMPLAINT_RULES = {
    "payment_not_received": {
        "auto_escalate_after_days": 3,
        "severity_if_escalated": "high",
        "default_severity": "medium",
    },
    "quality_mismatch": {
        "auto_escalate_after_days": None,
        "severity_if_escalated": None,
        "default_severity": "medium",
    },
    "quantity_mismatch": {
        "auto_escalate_after_days": None,
        "severity_if_escalated": None,
        "default_severity": "medium",
    },
    "buyer_unresponsive": {
        "auto_escalate_after_days": 2,
        "severity_if_escalated": "high",
        "default_severity": "low",
    },
    "logistics_delay": {
        "auto_escalate_after_days": 5,
        "severity_if_escalated": "medium",
        "default_severity": "low",
    },
    "other": {
        "auto_escalate_after_days": None,
        "severity_if_escalated": None,
        "default_severity": "low",
    },
}


def generate_ticket_id(now: Optional[datetime] = None, rng: Optional[random.Random] = None) -> str:
    """now/rng injectable so tests can assert exact output instead of
    just pattern-matching."""
    now = now or datetime.now()
    rng = rng or random
    suffix = "".join(rng.choices(string.ascii_uppercase + string.digits, k=6))
    return f"GRV-{now.strftime('%Y%m%d')}-{suffix}"


def evaluate_grievance(complaint_type: str, days_since_issue: int = 0,
                        now: Optional[datetime] = None, rng: Optional[random.Random] = None) -> Dict:
    """Same logic as grievance_rule_evaluator() in grievance_rules_tool.py."""
    raw_key = complaint_type.strip().lower().replace(" ", "_")
    matched_known_category = raw_key in COMPLAINT_RULES
    rule = COMPLAINT_RULES.get(raw_key, COMPLAINT_RULES["other"])
    # BUGFIX vs. original grievance_rules_tool.py: that version labeled the
    # ticket with the raw (possibly unrecognized) string even when it had
    # silently fallen back to the "other" rule -- e.g. a typo'd category
    # would get the "other" severity/escalation behavior but still display
    # as if it were a matched category. Caught by test_unknown_category_
    # falls_back_to_other. Fix: report "other" as complaint_type whenever
    # we actually used the "other" rule, and keep the raw input separately
    # for traceability/debugging.
    effective_key = raw_key if matched_known_category else "other"

    escalated = (
        rule["auto_escalate_after_days"] is not None
        and days_since_issue >= rule["auto_escalate_after_days"]
    )
    severity = rule["severity_if_escalated"] if escalated else rule["default_severity"]

    return {
        "ticket_id": generate_ticket_id(now, rng),
        "complaint_type": effective_key,
        "raw_complaint_type_input": complaint_type,
        "matched_known_category": matched_known_category,
        "days_since_issue": days_since_issue,
        "escalated": escalated,
        "severity": severity,
        "rule_applied": rule,
        "status": "escalated_to_admin_review" if escalated else "open_awaiting_response",
    }


_CATEGORY_LABELS = {
    "payment_not_received": "payment not received",
    "quality_mismatch": "a quality mismatch",
    "quantity_mismatch": "a quantity mismatch",
    "buyer_unresponsive": "the buyer being unresponsive",
    "logistics_delay": "a logistics delay",
    "other": "your complaint",
}


def generate_farmer_message(result: Dict) -> str:
    """
    Deterministic, templated explanation of a grievance result -- NO LLM
    involved. This exists because a rules-table outcome (severity,
    escalation, ticket ID) is already fully explainable from the rule
    itself; asking an LLM to "explain" it adds a point of failure and a
    cost for zero benefit. An agent MAY still rephrase this in Marathi or
    a warmer tone downstream, but the facts stated here are exactly the
    facts in `result` -- nothing is invented.
    """
    label = _CATEGORY_LABELS.get(result["complaint_type"], "your complaint")
    ticket = result["ticket_id"]
    if result["escalated"]:
        return (
            f"We've logged your report about {label} as ticket {ticket}. "
            f"Because it's been {result['days_since_issue']} day(s) since this issue started, "
            f"it has been automatically escalated for admin review, with {result['severity']} priority. "
            f"You'll be contacted about next steps."
        )
    return (
        f"We've logged your report about {label} as ticket {ticket}, marked {result['severity']} priority. "
        f"It's open and awaiting a response. If this isn't resolved soon, "
        f"following up with the ticket number above will help our team locate it quickly."
    )

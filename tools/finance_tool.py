"""
finance_tool.py
-----------------
The ONE place net realization arithmetic happens. Every other module
(matching engine, logistics agent, orchestrator) must call this instead
of re-deriving Gross - Transport - Storage inline, so there is exactly
one formula to audit.
"""

from typing import Optional, Dict


def calculate_net_realization(
    offer_price_per_quintal: float,
    quantity_quintals: float,
    transport_cost: float = 0.0,
    storage_cost: float = 0.0,
    other_costs: float = 0.0,
    platform_fee_pct: float = 0.0,
) -> Dict:
    """
    Args:
        offer_price_per_quintal: Buyer's quoted price, INR per quintal.
        quantity_quintals: Lot size in quintals.
        transport_cost: Total (not per-km) estimated transport cost, INR.
        storage_cost: Total estimated storage cost if the lot was held
            before this sale, INR (0 if sold immediately).
        other_costs: Any other flat deduction (e.g. grading fee), INR.
        platform_fee_pct: Optional platform commission as a percent of
            gross revenue (0 for this prototype -- no live payment
            infra exists yet per the blueprint's payment section).

    Returns:
        dict with every intermediate figure shown, so nothing is a black
        box: gross_revenue, each cost line, platform_fee, net_realization,
        and net_realization_per_quintal for apples-to-apples buyer
        comparison regardless of lot size.
    """
    if quantity_quintals < 0:
        raise ValueError("quantity_quintals cannot be negative")
    if offer_price_per_quintal < 0:
        raise ValueError("offer_price_per_quintal cannot be negative")

    gross_revenue = round(offer_price_per_quintal * quantity_quintals, 2)
    platform_fee = round(gross_revenue * (platform_fee_pct / 100), 2)
    total_costs = round(transport_cost + storage_cost + other_costs + platform_fee, 2)
    net_realization = round(gross_revenue - total_costs, 2)
    net_per_quintal = round(net_realization / quantity_quintals, 2) if quantity_quintals else 0.0

    return {
        "offer_price_per_quintal": offer_price_per_quintal,
        "quantity_quintals": quantity_quintals,
        "gross_revenue": gross_revenue,
        "transport_cost": round(transport_cost, 2),
        "storage_cost": round(storage_cost, 2),
        "other_costs": round(other_costs, 2),
        "platform_fee": platform_fee,
        "total_costs": total_costs,
        "net_realization": net_realization,
        "net_realization_per_quintal": net_per_quintal,
    }


def compare_offers(offers: list) -> list:
    """
    Convenience helper: takes a list of dicts, each with at minimum
    offer_price_per_quintal/quantity_quintals/transport_cost (and
    optionally storage_cost/other_costs/label), runs calculate_net_realization
    on each, and returns them sorted by net_realization descending.

    This is the deterministic backbone behind "highest headline price
    isn't always the best deal" -- the ranking is arithmetic, not an LLM
    judgment call.
    """
    results = []
    for o in offers:
        calc = calculate_net_realization(
            offer_price_per_quintal=o["offer_price_per_quintal"],
            quantity_quintals=o["quantity_quintals"],
            transport_cost=o.get("transport_cost", 0.0),
            storage_cost=o.get("storage_cost", 0.0),
            other_costs=o.get("other_costs", 0.0),
            platform_fee_pct=o.get("platform_fee_pct", 0.0),
        )
        calc["label"] = o.get("label")
        results.append(calc)
    results.sort(key=lambda r: r["net_realization"], reverse=True)
    return results

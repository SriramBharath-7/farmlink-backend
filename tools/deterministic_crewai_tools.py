"""
deterministic_crewai_tools.py
--------------------------------
Thin @tool wrappers so CrewAI agents can call the deterministic engines
directly, instead of being asked to "analyse the price trend" in prose.

Drop-in replacement/addition alongside the existing mandi_price_tool.py,
buyer_db_tool.py, logistics_tools.py -- those keep doing raw data
fetch/lookup; these do the CALCULATION step that was previously missing,
so the agent's prompt can shrink to "call this tool, then explain its
output" rather than "compute this yourself."
"""

import json
from crewai.tools import tool
from tools.forecast_tool import forecast_price
from tools.finance_tool import calculate_net_realization
from tools.matching_engine import rank_matched_buyers
from tools.decision_pipeline import build_sell_decision


@tool("Price Forecast Calculator")
def price_forecast_calculator(mandi_price_records_json: str, horizon_days: int = 3) -> str:
    """
    Computes a deterministic price forecast (moving averages, trend,
    volatility-based expected range, documented confidence) from mandi
    price records. Takes the JSON string returned by the Mandi Price
    Lookup tool's "records" field -- do NOT estimate a trend yourself,
    call this tool with the records you already fetched.

    Args:
        mandi_price_records_json: JSON string of the list of price
            records (each with arrival_date and modal_price).
        horizon_days: How many days ahead to project (default 3).

    Returns:
        A JSON string with current_modal, moving averages, trend,
        expected_range, confidence, and the exact method/confidence
        formula used. If insufficient_data is true, you MUST tell the
        farmer data is too thin for a forecast rather than guessing.
    """
    records = json.loads(mandi_price_records_json)
    return json.dumps(forecast_price(records, horizon_days=horizon_days), ensure_ascii=False)


@tool("Net Realization Calculator")
def net_realization_calculator(
    offer_price_per_quintal: float,
    quantity_quintals: float,
    transport_cost: float = 0.0,
    storage_cost: float = 0.0,
) -> str:
    """
    Computes Gross Revenue minus Transport/Storage costs = Net Realization.
    ALWAYS call this instead of doing this arithmetic yourself -- a
    higher headline offer price does not always win once transport is
    netted out, and this tool is the only source of truth for that
    comparison.

    Returns:
        A JSON string with gross_revenue, each cost line, and
        net_realization (total and per-quintal).
    """
    return json.dumps(
        calculate_net_realization(
            offer_price_per_quintal, quantity_quintals, transport_cost, storage_cost
        ),
        ensure_ascii=False,
    )


@tool("Full Sell Decision Pipeline")
def full_sell_decision(commodity: str, district: str, quantity_quintals: float, grade: str = "A") -> str:
    """
    Runs the complete deterministic pipeline: price forecast, ranked
    buyer shortlist (weighted match score: price 35%/demand fit 20%/
    reliability 20%/distance 10%/quality 10%/quantity 5%), and net
    realization per buyer. This IS the recommendation -- your job is to
    explain WHY the top-ranked buyer/action was chosen using the
    score_breakdown and price_forecast fields already in the result, in
    plain farmer-readable language. Do NOT recompute or override
    match_score, net_realization, or the recommended_action -- copy them
    verbatim into your explanation.

    Args:
        commodity: Crop name, e.g. "Onion".
        district: Farmer's district.
        quantity_quintals: Lot size.
        grade: "A", "B", or "C".

    Returns:
        A JSON string: request, price_forecast, recommended_action,
        buyer_shortlist (ranked, with score_breakdown per buyer),
        data_status_summary (what's live/synthetic/derived).
    """
    return json.dumps(
        build_sell_decision(commodity, district, quantity_quintals, grade),
        ensure_ascii=False,
    )

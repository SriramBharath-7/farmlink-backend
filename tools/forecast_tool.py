"""
forecast_tool.py
------------------
Deterministic price forecasting. NO LLM CALLS HAPPEN HERE.

This directly answers the blueprint's requirement (section 14/15):
"Do NOT allow the LLM to invent numerical forecasts. Numerical values must
come from the backend/data/model. The LLM should explain the results."

Method: transparent statistical baseline (moving averages + trend +
volatility-based range), NOT a fabricated ML model. This is intentional --
a documented, auditable method beats an opaque "AI predicted X" claim for
a judged/demo context.

Every number this module returns can be recomputed by hand from the input
records. There is no hidden randomness and no LLM in the loop.
"""

import statistics
from datetime import datetime
from typing import List, Dict, Optional


def _parse_date(raw: str) -> datetime:
    return datetime.strptime(raw, "%d/%m/%Y")


def _sorted_ascending(records: List[Dict]) -> List[Dict]:
    return sorted(records, key=lambda r: _parse_date(r["arrival_date"]))


def forecast_price(records: List[Dict], horizon_days: int = 3) -> Dict:
    """
    Computes a deterministic price forecast from a list of mandi price
    records (each must have "arrival_date" as "DD/MM/YYYY" and
    "modal_price" as a float).

    Args:
        records: Historical price records for ONE commodity (optionally
            already filtered to one district). Order doesn't matter --
            this function sorts internally.
        horizon_days: How many days forward the expected_range projects.
            Kept small (default 3) because the underlying method is a
            short-horizon trend extrapolation, not a long-range model --
            claiming multi-week accuracy from 14-20 daily points would be
            methodologically dishonest.

    Returns:
        A dict with every field fully derived from the input records.
        If there isn't enough data, `insufficient_data` is set True and
        the caller (agent/API) MUST surface that rather than silently
        proceeding as if a real forecast exists.
    """
    if not records:
        return {
            "insufficient_data": True,
            "reason": "no_records_provided",
            "record_count": 0,
        }

    ordered = _sorted_ascending(records)
    prices = [float(r["modal_price"]) for r in ordered]
    dates = [r["arrival_date"] for r in ordered]
    n = len(prices)

    current_modal = prices[-1]
    most_recent_date = dates[-1]

    MIN_RECORDS_FOR_TREND = 3
    if n < MIN_RECORDS_FOR_TREND:
        return {
            "insufficient_data": True,
            "reason": f"only_{n}_records_minimum_{MIN_RECORDS_FOR_TREND}_required",
            "record_count": n,
            "current_modal": round(current_modal, 2),
            "most_recent_date": most_recent_date,
        }

    def moving_average(window: int) -> Optional[float]:
        if n < 1:
            return None
        w = min(window, n)
        return round(sum(prices[-w:]) / w, 2)

    ma_7 = moving_average(7)
    ma_14 = moving_average(14)

    # Trend: compare current price to the 7-day (or shorter, if less data)
    # moving average that EXCLUDES the current point, so we're comparing
    # "today" against "the recent baseline" rather than against a window
    # that already contains today (which would understate any move).
    baseline_window = prices[-8:-1] if n >= 8 else prices[:-1]
    baseline = sum(baseline_window) / len(baseline_window) if baseline_window else current_modal
    change_percent = round(((current_modal - baseline) / baseline) * 100, 2) if baseline else 0.0

    TREND_THRESHOLD_PCT = 1.5  # documented, not tuned per-commodity
    if change_percent > TREND_THRESHOLD_PCT:
        trend = "UPWARD"
    elif change_percent < -TREND_THRESHOLD_PCT:
        trend = "DOWNWARD"
    else:
        trend = "STABLE"

    # Volatility: coefficient of variation of day-over-day % changes.
    # This is the basis for both the expected range width AND the
    # confidence score -- high day-to-day noise widens the range and
    # lowers confidence, which is the honest way to express uncertainty
    # rather than a flat "78% confidence" pulled from nowhere.
    daily_changes_pct = []
    for i in range(1, n):
        prev, cur = prices[i - 1], prices[i]
        if prev:
            daily_changes_pct.append((cur - prev) / prev * 100)
    volatility = round(statistics.pstdev(daily_changes_pct), 3) if len(daily_changes_pct) >= 2 else 0.0

    # Expected range: extrapolate the measured trend forward by
    # horizon_days, then widen by the measured volatility. This is a
    # linear extrapolation of a short-window trend -- explicitly NOT a
    # claim of causal forecasting.
    daily_drift_pct = change_percent / 7 if change_percent else 0.0
    projected_center = current_modal * (1 + (daily_drift_pct * horizon_days) / 100)
    spread = current_modal * (volatility / 100) * max(1, horizon_days ** 0.5)
    expected_min = round(max(0, projected_center - spread), 2)
    expected_max = round(projected_center + spread, 2)

    # Confidence methodology (fully documented, deterministic):
    #   base 0.85, minus a penalty for high volatility, minus a penalty
    #   for thin data (fewer than 14 points). Floored at 0.30, capped at
    #   0.90 -- we never claim near-certainty from a moving average.
    data_penalty = 0.0 if n >= 14 else (14 - n) * 0.02
    volatility_penalty = min(0.40, volatility * 0.05)
    confidence = round(max(0.30, min(0.90, 0.85 - data_penalty - volatility_penalty)), 2)

    freshness_days = (datetime.now() - _parse_date(most_recent_date)).days

    return {
        "insufficient_data": False,
        "record_count": n,
        "current_modal": round(current_modal, 2),
        "most_recent_date": most_recent_date,
        "data_freshness_days": freshness_days,
        "moving_average_7d": ma_7,
        "moving_average_14d": ma_14,
        "change_percent_vs_baseline": change_percent,
        "trend": trend,
        "volatility_pct": volatility,
        "horizon_days": horizon_days,
        "expected_range": {"min": expected_min, "max": expected_max},
        "confidence": confidence,
        "method": (
            f"Linear extrapolation of {min(n, 8)}-day trend "
            f"({'>=8' if n >= 8 else str(n)}-point baseline), "
            f"range widened by measured day-to-day volatility "
            f"({volatility}% stdev of daily % change)."
        ),
        "confidence_methodology": (
            "confidence = clamp(0.85 - data_penalty - volatility_penalty, 0.30, 0.90); "
            "data_penalty = 0.02 per record short of 14; "
            "volatility_penalty = min(0.40, volatility_pct * 0.05)"
        ),
    }

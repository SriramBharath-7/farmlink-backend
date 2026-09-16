"""
api_v2.py
-----------
Structured replacement for the free-text {"result": "..."} endpoints in
the original api.py. Every endpoint here returns a typed Pydantic model
built directly from the deterministic engines (forecast_tool,
finance_tool, matching_engine, logistics_core, grievance_core) --
numbers in the response are never something an LLM produced.

LLM narration (a short farmer-readable explanation) is OPTIONAL and
gracefully degraded: if crewai/the configured LLM provider isn't
available or errors out, the endpoint still returns the full structured
result with `llm_explanation_status` explaining why reasoning_text is
null, instead of failing the whole request. This matches the project's
existing fallback-first philosophy (same shape as the mandi price tool's
live/synthetic fallback) applied to the agent layer itself.

The original api.py is left untouched -- this is an additive file, not
a replacement, per "don't delete functional features."
"""

from typing import List, Optional, Dict, Any
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tools.decision_pipeline import build_sell_decision, build_matching_result, build_logistics_result, build_market_discovery
from tools.grievance_core import evaluate_grievance, generate_farmer_message

app = FastAPI(
    title="FarmLink Agent Layer v2 (structured)",
    description="Deterministic-first endpoints: every number is computed by backend "
                "tools, never invented by an LLM. LLM narration is optional and "
                "gracefully degrades if unavailable.",
    version="2.0.0",
)

# CORS: required for ANY browser-based frontend (including a Capacitor
# app's WebView, which browsers treat as a real origin, e.g.
# capacitor://localhost or http://localhost) to call this API from a
# different origin than the backend itself. Without this, every fetch()
# call from the frontend fails silently with a CORS error before the
# request even reaches an endpoint -- not a bug in the frontend code.
#
# CORS_ALLOWED_ORIGINS is a comma-separated env var so this can be
# tightened per deployment (dev: allow localhost; prod: allow only your
# actual deployed frontend origin(s) -- do NOT leave allow_origins=["*"]
# with allow_credentials=True in production, browsers reject that
# combination anyway and it's bad practice even where they don't).
_default_dev_origins = [
    "http://localhost:3000",       # next dev
    "http://localhost",            # Capacitor Android WebView default
    "capacitor://localhost",       # Capacitor iOS WebView scheme
    "http://127.0.0.1:3000",
]
_env_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
_allowed_origins = [o.strip() for o in _env_origins.split(",") if o.strip()] or _default_dev_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------
# Request models (same field names as the original api.py for drop-in
# frontend compatibility)
# ---------------------------------------------------------------------

class PriceRequest(BaseModel):
    crop: str = Field(..., examples=["Onion"])
    state: str = Field(..., examples=["Maharashtra"])
    district: str = Field(..., examples=["Nashik"])
    quantity_quintals: float = Field(..., examples=[20])
    use_live_routing: bool = False


class MatchingRequest(BaseModel):
    crop: str
    district: str
    quantity_quintals: float
    grade: str = "A"
    use_live_routing: bool = False


class LogisticsRequest(BaseModel):
    origin_district: str
    destination_district: str
    quantity_quintals: float
    use_live_routing: bool = False


class GrievanceRequest(BaseModel):
    complaint_type: str
    days_since_issue: int = 0
    complaint_text: str = ""


class SellDecisionRequest(BaseModel):
    crop: str
    state: str
    district: str
    quantity_quintals: float
    grade: str = "A"
    use_live_routing: bool = False
    include_llm_explanation: bool = True
    data_source_mode: str = "direct"  # "direct" | "adapter" | "postgres" -- see decision_pipeline.build_sell_decision


class MarketDiscoveryRequest(BaseModel):
    crop: str
    farmer_state: str
    farmer_district: str
    quantity_quintals: float
    limit: int = Field(default=10, ge=1, le=50)


# ---------------------------------------------------------------------
# Response models -- structured, typed, no free-text blobs standing in
# for data.
# ---------------------------------------------------------------------

class ScoreBreakdown(BaseModel):
    price_score: float
    demand_fit_score: float
    reliability_score: float
    distance_score: float
    quality_score: float
    quantity_score: float
    weights_applied: Dict[str, float]


class BuyerMatch(BaseModel):
    buyer_id: str
    buyer_name: str
    buyer_district: str
    offer_price_per_quintal: float
    meets_grade_requirement: bool
    match_score: float
    score_breakdown: ScoreBreakdown
    distance_km: Optional[float]
    transport_cost: float
    net_realization: float
    net_realization_per_quintal: float
    gross_revenue: float


class PriceForecast(BaseModel):
    insufficient_data: bool
    record_count: int
    current_modal: Optional[float] = None
    most_recent_date: Optional[str] = None
    data_freshness_days: Optional[int] = None
    moving_average_7d: Optional[float] = None
    moving_average_14d: Optional[float] = None
    change_percent_vs_baseline: Optional[float] = None
    trend: Optional[str] = None
    volatility_pct: Optional[float] = None
    horizon_days: Optional[int] = None
    expected_range: Optional[Dict[str, float]] = None
    confidence: Optional[float] = None
    method: Optional[str] = None
    confidence_methodology: Optional[str] = None
    data_status: Optional[str] = None
    source: Optional[str] = None
    widened_to_statewide: Optional[bool] = None
    reason: Optional[str] = None


class TransportQuote(BaseModel):
    provider_id: str
    name: str
    vehicle_types: List[str]
    rating: float
    estimated_total_cost_inr: float
    cost_per_quintal: float


class SellDecisionResponse(BaseModel):
    request: Dict[str, Any]
    generated_at: str
    price_forecast: PriceForecast
    recommended_action: str
    buyer_shortlist: List[BuyerMatch]
    excluded_buyers_count: int
    data_status_summary: Dict[str, str]
    reasoning_text: Optional[str] = None
    llm_explanation_status: str


class MatchingResponse(BaseModel):
    request: Dict[str, Any]
    generated_at: str
    reference_mandi_price: float
    buyer_shortlist: List[BuyerMatch]
    excluded_buyers_count: int
    data_status_summary: Dict[str, str]


class LogisticsResponse(BaseModel):
    origin: str
    destination: str
    quantity_quintals: float
    distance_km: float
    distance_source: str
    transport_quotes: List[TransportQuote]
    cheapest_transport_cost_inr: Optional[float]
    storage_alternative: Dict[str, Any]
    data_status_summary: Dict[str, str]


class GrievanceResponse(BaseModel):
    ticket_id: str
    complaint_type: str
    raw_complaint_type_input: str
    matched_known_category: bool
    days_since_issue: int
    escalated: bool
    severity: str
    rule_applied: Dict[str, Any]
    status: str
    farmer_message: str


class MarketOpportunity(BaseModel):
    state: str
    district: str
    market: str
    scope: str
    distance_km: Optional[float]
    current_modal_price_per_quintal: float
    gross_market_value: float
    estimated_transport_cost: Optional[float]
    estimated_net_realization: Optional[float]
    transport_provider_id: Optional[str]
    price_forecast: PriceForecast


class MarketDiscoveryResponse(BaseModel):
    request: Dict[str, Any]
    generated_at: str
    markets_considered: int
    market_opportunities: List[MarketOpportunity]
    data_status_summary: Dict[str, str]


class MarketDataStatusResponse(BaseModel):
    source: str
    status: str
    last_successful_sync: Optional[str] = None
    latest_data_date: Optional[str] = None
    records_available: int
    states_with_real_data: int
    fresh_states: int
    stale_states: int
    failed_states: List[str]


class MarketLocation(BaseModel):
    state: str
    districts: List[str]


class MarketLocationsResponse(BaseModel):
    source: str
    locations: List[MarketLocation]


# ---------------------------------------------------------------------
# Optional LLM narration -- lazy import, never a hard dependency.
# ---------------------------------------------------------------------

def _try_llm_explain(decision: dict) -> (Optional[str], str):
    """
    Returns (reasoning_text_or_None, status_string). Never raises. If
    crewai/the configured provider isn't importable or errors, this
    degrades to (None, "unavailable: <reason>") rather than failing the
    whole request -- the deterministic result is still fully usable
    without a sentence of prose wrapped around it.
    """
    try:
        from agents.orchestrator_v2 import _explanation_task
        from agents.agent_definitions import build_price_intelligence_agent
        from crewai import Crew, Process

        agent = build_price_intelligence_agent()
        task = _explanation_task(agent, decision)
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        result = crew.kickoff()
        return str(result).strip(), "generated"
    except ImportError as e:
        return None, f"unavailable: crewai/LLM provider not installed ({e})"
    except Exception as e:
        return None, f"unavailable: LLM call failed ({type(e).__name__}: {e})"


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------

@app.post("/agents/price-intelligence", response_model=PriceForecast)
def price_intelligence(req: PriceRequest):
    decision = build_sell_decision(
        req.crop,
        req.district,
        req.quantity_quintals,
        "A",
        req.use_live_routing,
        state=req.state,
    )
    if decision.get("error"):
        raise HTTPException(status_code=422, detail=decision)
    return decision["price_forecast"]


@app.post("/agents/match-buyers", response_model=MatchingResponse)
def match_buyers(req: MatchingRequest):
    result = build_matching_result(req.crop, req.district, req.quantity_quintals, req.grade, req.use_live_routing)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result)
    return result


@app.post("/agents/logistics", response_model=LogisticsResponse)
def logistics(req: LogisticsRequest):
    result = build_logistics_result(req.origin_district, req.destination_district,
                                     req.quantity_quintals, req.use_live_routing)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result)
    return result


@app.post("/agents/grievance", response_model=GrievanceResponse)
def grievance(req: GrievanceRequest):
    result = evaluate_grievance(req.complaint_type, req.days_since_issue)
    result["farmer_message"] = generate_farmer_message(result)
    return result


def _open_db_session():
    """
    Lazy import + open, only when a request actually asks for
    data_source_mode="postgres" -- so api_v2.py has no hard dependency
    on sqlalchemy/DATABASE_URL for anyone only ever using "direct" or
    "adapter" mode, matching this file's existing lazy-import
    conventions for optional pieces (LLM narration, etc.).

    Raises RuntimeError with a clear message (caught by the caller and
    turned into a 503) if DATABASE_URL isn't set or the DB is
    unreachable -- never silently falls back to JSON, since that would
    misrepresent Postgres-backed data as having been used when it wasn't.
    """
    from db.session import get_session_factory
    Session = get_session_factory()  # raises RuntimeError itself if DATABASE_URL unset
    return Session()


@app.post("/agents/market-discovery", response_model=MarketDiscoveryResponse)
def market_discovery(req: MarketDiscoveryRequest):
    """
    Discover real AGMARKNET-backed selling-market opportunities from the
    farmer's produce location. The farmer supplies the origin; FarmLink
    discovers candidate destinations.
    """
    db_session = None

    try:
        try:
            db_session = _open_db_session()
            result = build_market_discovery(
                commodity=req.crop,
                farmer_state=req.farmer_state,
                farmer_district=req.farmer_district,
                quantity_quintals=req.quantity_quintals,
                db_session=db_session,
                limit=req.limit,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "postgres_unavailable",
                    "reason": f"{type(e).__name__}: {e}",
                },
            )
    finally:
        if db_session is not None:
            engine = db_session.get_bind()
            db_session.close()
            engine.dispose()

    if result.get("error"):
        raise HTTPException(status_code=422, detail=result)

    return result


@app.post("/agents/sell-decision", response_model=SellDecisionResponse)
def sell_decision(req: SellDecisionRequest):
    """
    The full pipeline: price forecast + ranked buyer shortlist + net
    realization, in one call. This is the endpoint that best demonstrates
    the "backend calculates, agent explains" architecture -- every number
    in the response is fixed before reasoning_text is (optionally)
    generated, and reasoning_text cannot alter any of them.
    """
    if req.data_source_mode not in ("direct", "adapter", "postgres"):
        raise HTTPException(status_code=422, detail={"error": "invalid data_source_mode"})

    db_session = None
    try:
        if req.data_source_mode == "postgres":
            try:
                db_session = _open_db_session()
                decision = build_sell_decision(
                    req.crop, req.district, req.quantity_quintals, req.grade,
                    req.use_live_routing, req.data_source_mode, db_session=db_session,
                    state=req.state,
                )
            except HTTPException:
                raise
            except Exception as e:
                # Catches BOTH session-creation failures (bad/missing
                # DATABASE_URL) AND failures during the actual query
                # (e.g. Postgres reachable at session-open time but the
                # connection drops before the query runs -- SQLAlchemy
                # engines connect lazily, so a failure here is common,
                # not an edge case, and must not leak a raw 500/traceback).
                raise HTTPException(
                    status_code=503,
                    detail={"error": "postgres_unavailable", "reason": f"{type(e).__name__}: {e}"},
                )
        else:
            decision = build_sell_decision(
                req.crop, req.district, req.quantity_quintals, req.grade,
                req.use_live_routing, req.data_source_mode, db_session=None,
                state=req.state,
            )
    finally:
        if db_session is not None:
            # REAL BUG FIXED HERE (same class of leak as
            # db/seed_from_json.py's run() -- see AUDIT_REPORT.md
            # COMPONENT #3): db_session.close() only returns the
            # connection to SQLAlchemy's pool, it does NOT dispose the
            # engine _open_db_session() created fresh for this request.
            # Every postgres-mode request was leaking one engine's
            # worth of pooled connections, which eventually blocked
            # test fixtures' `DROP DATABASE` at teardown with
            # intermittent, hard-to-reproduce failures. Dispose the
            # engine the session was actually bound to, not just the
            # session itself.
            engine = db_session.get_bind()
            db_session.close()
            engine.dispose()

    if decision.get("error"):
        raise HTTPException(status_code=422, detail=decision)

    if req.include_llm_explanation:
        reasoning, status = _try_llm_explain(decision)
        decision["reasoning_text"] = reasoning
        decision["llm_explanation_status"] = status
    else:
        decision["reasoning_text"] = None
        decision["llm_explanation_status"] = "not_requested"

    return decision


@app.get("/market-data/locations", response_model=MarketLocationsResponse)
def market_data_locations():
    """
    Return canonical state -> district choices backed by persisted REAL
    AGMARKNET observations. SYNTHETIC rows are excluded by the repository
    query so the frontend only offers locations represented by real
    government market-price data.
    """
    db_session = None

    try:
        try:
            db_session = _open_db_session()
            from db.repositories import get_market_locations
            return {
                "source": "AGMARKNET",
                "locations": get_market_locations(db_session),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "postgres_unavailable",
                    "reason": f"{type(e).__name__}: {e}",
                },
            )
    finally:
        if db_session is not None:
            engine = db_session.get_bind()
            db_session.close()
            engine.dispose()


@app.get("/market-data/status", response_model=MarketDataStatusResponse)
def market_data_status():
    """
    Report health of persisted REAL AGMARKNET market data.

    This endpoint never contacts AGMARKNET and never treats SYNTHETIC
    rows as production availability. State health is derived from each
    state's latest IngestionRun so one state's success cannot hide
    another state's failure.
    """
    db_session = None

    try:
        try:
            db_session = _open_db_session()
            from db.repositories import get_market_data_status
            return get_market_data_status(db_session)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "postgres_unavailable",
                    "reason": f"{type(e).__name__}: {e}",
                },
            )
    finally:
        if db_session is not None:
            engine = db_session.get_bind()
            db_session.close()
            engine.dispose()


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0", "deterministic_engines": [
        "forecast_tool", "finance_tool", "matching_engine", "logistics_core", "grievance_core",
    ]}

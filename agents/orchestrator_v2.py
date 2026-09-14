"""
orchestrator_v2.py
----------------------
Belt-and-suspenders version of the sell-decision flow.

Design: the numeric result is computed ONCE, in plain Python, before the
agent ever runs. The agent's task is narrowed to producing a single
"reasoning_text" field in farmer-readable language (and can be asked for
a Marathi translation the same way). After the agent returns, this
module OVERWRITES every numeric field in its output with the
directly-computed values -- so even if a future prompt regression causes
the LLM to "helpfully" recompute or round differently, the API response
sent to the frontend is guaranteed to match the deterministic pipeline
exactly.

This is intentionally more defensive than relying on prompt wording
alone ("copy this verbatim") -- prompts are not a reliability guarantee
in an agentic system judges will stress-test.
"""

from crewai import Task, Crew, Process
from tools.decision_pipeline import build_sell_decision
from agents.agent_definitions import build_price_intelligence_agent


def _explanation_task(agent, decision: dict):
    return Task(
        description=(
            "You are given a fully computed sell-decision result as JSON below. "
            "Do NOT recompute, adjust, or second-guess any number in it. Your only "
            "job is to write a 2-4 sentence, farmer-readable explanation of the "
            "recommended_action, referencing the price_forecast.trend and the top "
            "buyer's net_realization from buyer_shortlist[0] if one exists.\n\n"
            f"COMPUTED RESULT (ground truth, do not alter):\n{decision}\n\n"
            "Respond with ONLY the explanation sentences. No JSON, no markdown, "
            "no restated numbers that don't appear in the data above."
        ),
        expected_output="2-4 plain sentences explaining the decision above, no invented figures.",
        agent=agent,
    )


def run_sell_decision(crop: str, district: str, quantity_quintals: float, grade: str = "A") -> dict:
    """
    The recommended replacement for run_price_intelligence + run_matching
    + run_full_orchestration combined. Returns one structured object
    whose numeric fields are guaranteed to equal build_sell_decision's
    direct output, regardless of what the LLM does in reasoning_text.
    """
    decision = build_sell_decision(crop, district, quantity_quintals, grade)
    if decision.get("error"):
        return decision  # nothing for an agent to explain if data is unavailable

    agent = build_price_intelligence_agent()
    task = _explanation_task(agent, decision)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    llm_result = crew.kickoff()

    # The LLM only ever contributes this one field. Every number in
    # `decision` was computed before the agent ran and is untouched here.
    decision["reasoning_text"] = str(llm_result).strip()
    return decision

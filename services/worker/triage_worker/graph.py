"""Analyst agent.

Walking-skeleton version: returns a clearly labelled placeholder that escalates, so the pipeline
can be exercised end to end without ever producing a confident answer.

Stage 4 replaces `analyse` with a LangGraph graph:
    classify -> extract entities -> search_corpus -> read -> "what modifies this?"
    -> find_references / get_clause -> search again -> draft position with citations
    -> call verifier (separate service) -> route (routing.decide_route)
Bounds: settings.max_agent_steps, settings.max_tool_calls, settings.run_timeout_seconds.
Any exceeded bound, failed verification (twice) or missing evidence must escalate.
"""


def analyse(run_id: str, fnol: dict) -> dict:
    return {
        "run_id": run_id,
        "status": "escalated",
        "coverage": {
            "position": "escalate",
            "summary": "Placeholder pipeline: the analyst agent is not implemented yet.",
            "flags": ["skeleton"],
            "citations": [],
            "verified": False,
            "prompt_version": None,
        },
        "routing": {
            "route": "human_review",
            "reason": "Placeholder pipeline; a human must review.",
            "escalated": True,
            "flags": ["skeleton"],
        },
    }

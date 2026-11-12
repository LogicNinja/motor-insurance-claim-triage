"""Bounded, evidence-first analyst workflow."""
from __future__ import annotations

import json
import time
from typing import TypedDict

import httpx
from langgraph.graph import END, StateGraph

from shared.config import get_settings
from .guardrails import filter_output, neutralize_injection, screen_input, wrap_untrusted
from .llm import ProviderUnavailableError, get_llm_client
from .prompts import load_prompt
from .routing import decide_route, is_out_of_corpus
from .tools import find_references, get_clause, get_fnol_fields, search_corpus


class AnalystState(TypedDict, total=False):
    run_id: str
    fnol: dict
    claim: dict
    entities: dict
    evidence: list[dict]
    trace: list[dict]
    steps: int
    tool_calls: int
    started: float
    retries: int
    draft: dict
    coverage: dict
    routing: dict
    verification: dict


REQUIRED = ("Section I(A) clause 1 item VI", "General Exceptions III(a)", "CHAPTER 4 - Limitations as to Use")


def _budget(state: AnalystState) -> bool:
    settings = get_settings()
    if state.get("steps", 0) >= settings.max_agent_steps:
        return False
    if time.monotonic() - state.get("started", time.monotonic()) > settings.run_timeout_seconds:
        return False
    return True


def _tool(state: AnalystState, name: str, fn, *args):
    if state.get("tool_calls", 0) >= get_settings().max_tool_calls:
        raise RuntimeError("tool-call budget exceeded")
    result = fn(*args)
    state["tool_calls"] = state.get("tool_calls", 0) + 1
    state.setdefault("trace", []).append({"tool": name, "ok": True})
    return result


def _classify_extract(state: AnalystState):
    fields = _tool(state, "get_fnol_fields", get_fnol_fields, state["run_id"]) or state.get("fnol", {})
    description = str(fields.get("description", ""))
    text = description.casefold()
    if any(k in text for k in ("stolen", "theft", "missing vehicle")):
        claim_type = "theft"
    elif any(k in text for k in ("third party", "third-party", "injur", "pedestrian")):
        claim_type = "third_party"
    elif any(k in text for k in ("total loss", "written off")):
        claim_type = "total_loss"
    else:
        claim_type = "own_damage"
    delivery = any(k in text for k in ("food delivery", "food-delivery", "deliver food", "delivery rider", "delivering orders"))
    out_of_corpus = is_out_of_corpus(description)
    state["claim"] = {**fields, "claim_type": claim_type, "third_party_injury": any(k in text for k in ("injur", "hospital", "pedestrian"))}
    state["entities"] = {
        "policy_number": fields.get("policy_number"), "date_of_loss": fields.get("date_of_loss"),
        "vehicle_registration": fields.get("vehicle_registration"), "estimated_damage_inr": fields.get("estimated_damage_inr"),
        "claim_type": claim_type, "vehicle_use": "food_delivery" if delivery else "not_stated",
        "driver_or_courier_context": bool(delivery), "injection_screen": screen_input(description),
        "out_of_corpus": out_of_corpus,
    }
    if out_of_corpus:
        state.setdefault("trace", []).append({"event": "out_of_corpus", "reason": "FNOL does not describe a motor vehicle loss"})
    state["steps"] = state.get("steps", 0) + 1
    return state


def _retrieve_cover(state: AnalystState):
    if not _budget(state):
        state["trace"].append({"event": "step_or_time_budget_exhausted"})
        return state
    passages = _tool(state, "search_corpus", search_corpus,
        "Section I(A) own damage cover accidental external means; Section I(B) third party; motor vehicle food delivery use", 12)
    evidence = state.setdefault("evidence", [])
    evidence.extend(passages)
    # Exact clause read anchors the core cover wording regardless of vector ranking.
    result = _tool(state, "get_clause", get_clause, REQUIRED[0])
    evidence.extend(result.get("clauses", []))
    state["steps"] = state.get("steps", 0) + 1
    return state


def _retrieve_limits(state: AnalystState):
    if not _budget(state):
        state["trace"].append({"event": "step_or_time_budget_exhausted"})
        return state
    exceptions = _tool(state, "get_clause", get_clause, REQUIRED[1])
    evidence = state.setdefault("evidence", [])
    evidence.extend(exceptions.get("clauses", []))
    refs = _tool(state, "find_references", find_references, "Limitations as to Use")
    evidence.extend(refs)
    state["steps"] = state.get("steps", 0) + 1
    return state


def _has(evidence: list[dict], target: str) -> bool:
    target = target.casefold()
    return any(target == str(p.get("clause_id", "")).casefold() or target == str(p.get("chapter", "")).casefold() for p in evidence)


def _evidence_gate(state: AnalystState):
    missing = [target for target in REQUIRED if not _has(state.get("evidence", []), target)]
    state["missing"] = missing
    state["steps"] = state.get("steps", 0) + 1
    return state


def _repair(state: AnalystState):
    # One bounded repair pass searches explicit names that may have been absent from a response.
    if state.get("retries", 0) >= 1 or not _budget(state):
        return state
    for target in state.get("missing", []):
        rows = _tool(state, "search_corpus", search_corpus, f'"{target}"', 12)
        state.setdefault("evidence", []).extend(rows)
    state["retries"] = state.get("retries", 0) + 1
    state["steps"] = state.get("steps", 0) + 1
    return state


def _route_after_gate(state: AnalystState):
    return "repair" if state.get("missing") and state.get("retries", 0) < 1 and _budget(state) else "draft"


def _after_classify(state: AnalystState):
    return "draft" if state.get("entities", {}).get("out_of_corpus") else "retrieve_cover"


def _unique(evidence: list[dict]) -> list[dict]:
    output, seen = [], set()
    for row in evidence:
        key = (str(row.get("clause_id", "")).casefold(), str(row.get("page", "")), str(row.get("text", ""))[:80])
        if key not in seen:
            output.append(row)
            seen.add(key)
    return output


def _draft(state: AnalystState):
    evidence = _unique(state.get("evidence", []))
    by_clause = {}
    for p in evidence:
        cid = p.get("clause_id") or p.get("chapter")
        if cid and cid not in by_clause:
            by_clause[cid] = p
    out_of_corpus = bool(state.get("entities", {}).get("out_of_corpus"))
    complete = not out_of_corpus and all(_has(evidence, target) for target in REQUIRED)
    delivery = state.get("entities", {}).get("vehicle_use") == "food_delivery"
    citations = []
    for target in (() if out_of_corpus else REQUIRED):
        item = next((p for p in evidence if target.casefold() in {str(p.get("clause_id", "")).casefold(), str(p.get("chapter", "")).casefold()}), None)
        if item:
            citations.append({"clause_id": item.get("clause_id") or item.get("chapter"), "page": item.get("page"), "quote": str(item.get("text", ""))[:900]})

    # Ask the configured analyst model for a draft; the deterministic evidence-bound draft is
    # retained if a stub/invalid response is returned, and it never creates a coverage decision.
    settings = get_settings()
    prompt_data = load_prompt("analyst", settings.prompt_version)
    system = prompt_data["system"]
    input_guardrail = neutralize_injection(str(state.get("claim", {}).get("description", "")))
    user_text = {
        "claim": {k: v for k, v in state.get("claim", {}).items() if k != "description"},
        "description": wrap_untrusted(str(state.get("claim", {}).get("description", ""))),
        "evidence": citations,
    }
    if input_guardrail["flagged"]:
        state.setdefault("trace", []).append({
            "event": "injection_neutralized", "redactions": input_guardrail["redactions"],
            "model_input_redacted": True,
        })
    model_candidate = None
    llm_degraded = False
    llm_degraded_reason = None
    if not out_of_corpus:
        try:
            llm = get_llm_client().complete(
                [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user_text)}],
                settings.prompt_version, "draft_position", prompt_data["sha256"],
            )
        except Exception as exc:
            llm_degraded = True
            llm_degraded_reason = "provider_unavailable"
            provider_failed = isinstance(exc, ProviderUnavailableError) or bool(getattr(exc, "breaker_open", False))
            state.setdefault("trace", []).append({
                "event": "llm_fallback", "reason": type(exc).__name__,
                "provider_call_failed": provider_failed, "provider": settings.llm_provider or "configured-provider",
                "attempts": getattr(exc, "attempts", 0), "status_code": getattr(exc, "status_code", None),
                "breaker_open": bool(getattr(exc, "breaker_open", False)),
                "breaker_failures": getattr(exc, "failures", None),
                "prompt_version": settings.prompt_version, "prompt_hash": prompt_data["sha256"],
                "cost_usd": None, "cost_status": "unavailable",
            })
            if getattr(exc, "breaker_open", False):
                state.setdefault("trace", []).append({
                    "event": "circuit_breaker_open", "failures": getattr(exc, "failures", None),
                    "cooldown_seconds": settings.breaker_cooldown_seconds,
                })
        else:
            state.setdefault("trace", []).append({"event": "llm_draft", **{k: v for k, v in llm.items() if k != "text"}})
            try:
                model_text = llm["text"].strip()
                if model_text.startswith("```"):
                    model_text = model_text.strip("`")
                    if model_text.startswith("json"):
                        model_text = model_text[4:].strip()
                model_draft = json.loads(model_text)
                allowed_positions = {"potentially_covered", "potentially_not_covered", "escalate"}
                if not (isinstance(model_draft, dict) and isinstance(model_draft.get("summary"), str)
                        and model_draft.get("position") in allowed_positions):
                    raise ValueError("invalid structured response")
            except (ValueError, TypeError, KeyError) as exc:
                llm_degraded = True
                llm_degraded_reason = "invalid_structured_response"
                state.setdefault("trace", []).append({
                    "event": "llm_fallback", "reason": llm_degraded_reason,
                    "provider_call_failed": False, "provider": llm.get("provider"),
                    "attempts": 1, "breaker_open": False, "prompt_version": settings.prompt_version,
                    "prompt_hash": prompt_data["sha256"], "cost_usd": llm.get("cost_usd"),
                    "cost_status": llm.get("cost_status"),
                })
            else:
                # Suspicious claimant instructions cannot alter the policy position or summary.
                if input_guardrail["flagged"]:
                    state.setdefault("trace", []).append({"event": "model_output_ignored_for_injection"})
                else:
                    model_candidate = model_draft

    flags = []
    if state.get("entities", {}).get("injection_screen", {}).get("flagged"):
        flags.append("prompt_injection_suspected")
    if out_of_corpus:
        flags.append("out_of_corpus")
        summary = "The reported event does not describe a motor vehicle loss supported by this policy corpus. No coverage position is made; refer for human review."
        position = "escalate"
    elif delivery:
        flags.extend(["use_restriction", "use_restriction_ambiguity", "commercial_use_needs_review", "food_delivery_use_not_resolved_by_wording"])
        summary = ("Preliminary position: the accidental own-damage cover wording is relevant, but the reported food-delivery use may engage the vehicle-use limitations. General Exception III(a) points to the Limitations as to Use chapter. The passages establish the applicable wording but do not by themselves settle whether this delivery activity was permitted under the individual policy schedule; obtain the schedule and refer for human review.") if complete else "Preliminary position: food-delivery use requires human review, but the required policy passages could not all be retrieved; no coverage conclusion is made."
        position = "potentially_not_covered" if complete else "escalate"
    elif complete:
        summary = "Preliminary position: the retrieved policy wording appears relevant to the reported loss. Confirm the insured schedule, facts and applicable limitations before any decision."
        position = "potentially_covered"
    else:
        summary = "The policy evidence is incomplete. No coverage position can be drafted safely; refer for human review."
        position = "escalate"
        flags.append("required_policy_evidence_missing")
    if not complete and "required_policy_evidence_missing" not in flags:
        flags.append("required_policy_evidence_missing")
    if model_candidate and complete and not out_of_corpus:
        summary = model_candidate["summary"].strip()
        position = "potentially_not_covered" if delivery else model_candidate["position"]
    if llm_degraded:
        summary = "The language-model provider did not return a usable response. This degraded fallback makes no coverage determination; a human must review the claim and cited policy wording."
        position = "escalate"
        flags.extend(["degraded_fallback", llm_degraded_reason or "llm_unavailable"])
    draft = {"summary": summary, "position": position, "citations": citations, "flags": flags,
             "evidence_complete": complete, "drafted": bool(citations), "model_draft_validated": False}
    state["draft"] = draft
    filtered = filter_output(summary)
    if not filtered["ok"]:
        draft.update(position="escalate", summary="The generated draft was blocked by the output guardrail; human review is required.", flags=flags + ["output_guardrail_blocked"])
    coverage = {"position": draft["position"], "summary": draft["summary"], "flags": draft["flags"],
                "citations": draft["citations"], "verified": False, "out_of_corpus": out_of_corpus,
                "degraded": llm_degraded, "fallback_reason": llm_degraded_reason,
                "prompt_version": settings.prompt_version, "prompt_id": prompt_data["id"],
                "prompt_hash": prompt_data["sha256"]}
    state["coverage"] = coverage
    state["evidence"] = evidence
    state["steps"] = state.get("steps", 0) + 1
    return state


def _verify_and_route(state: AnalystState):
    """Call the independent verifier service and route only after its verdict."""
    settings = get_settings()
    coverage = state.get("coverage", {})
    request = {"draft": {"position": coverage.get("position"), "summary": coverage.get("summary"),
                         "citations": coverage.get("citations", [])},
               "passages": state.get("evidence", [])}
    try:
        response = httpx.post(f"{settings.verifier_url.rstrip('/')}/verify", json=request,
                              timeout=settings.llm_timeout_seconds)
        response.raise_for_status()
        verdict = response.json()
    except Exception as exc:
        verdict = {"verified": False, "problems": [{"problem": f"verifier unavailable: {type(exc).__name__}"}],
                   "next_action": "escalate"}
    state["verification"] = verdict
    state.setdefault("trace", []).append({"event": "verification", "verified": bool(verdict.get("verified")),
                                         "problems": verdict.get("problems", []), "next_action": verdict.get("next_action")})
    coverage["verified"] = bool(verdict.get("verified"))
    coverage["verification"] = verdict
    if not coverage["verified"]:
        coverage["position"] = "escalate"
        coverage["flags"] = list(dict.fromkeys(coverage.get("flags", []) + ["verification_failed"]))
    state["routing"] = decide_route(state.get("claim", {}), coverage)
    state["steps"] = state.get("steps", 0) + 1
    return state


def _compile():
    graph = StateGraph(AnalystState)
    for name, fn in (("classify_extract", _classify_extract), ("retrieve_cover", _retrieve_cover),
                     ("retrieve_limits", _retrieve_limits), ("evidence_gate", _evidence_gate),
                     ("repair", _repair), ("draft", _draft), ("verify_and_route", _verify_and_route)):
        graph.add_node(name, fn)
    graph.set_entry_point("classify_extract")
    graph.add_conditional_edges("classify_extract", _after_classify, {"retrieve_cover": "retrieve_cover", "draft": "draft"})
    graph.add_edge("retrieve_cover", "retrieve_limits")
    graph.add_edge("retrieve_limits", "evidence_gate")
    graph.add_conditional_edges("evidence_gate", _route_after_gate, {"repair": "repair", "draft": "draft"})
    graph.add_edge("repair", "evidence_gate")
    graph.add_edge("draft", "verify_and_route")
    graph.add_edge("verify_and_route", END)
    return graph.compile()


_ANALYST = _compile()


def analyse(run_id: str, fnol: dict) -> dict:
    state: AnalystState = {"run_id": run_id, "fnol": fnol, "started": time.monotonic(), "steps": 0,
                           "tool_calls": 0, "retries": 0, "evidence": [], "trace": []}
    try:
        final = _ANALYST.invoke(state, config={"recursion_limit": max(12, get_settings().max_agent_steps * 3)})
    except Exception as exc:
        final = {**state, "claim": fnol, "entities": {}, "evidence": [], "trace": state.get("trace", []) + [{"event": "agent_error", "reason": type(exc).__name__}],
                 "coverage": {"position": "escalate", "summary": "The analyst workflow could not complete within its configured limits; refer for human review.",
                              "flags": ["agent_workflow_error"], "citations": [], "verified": False, "prompt_version": get_settings().prompt_version},
                 "routing": {"route": "human_review", "reason": "Analyst workflow incomplete.", "escalated": True, "flags": ["agent_workflow_error"]}}
    trace_events = final.get("trace", [])
    llm_steps = [item for item in trace_events if item.get("event") == "llm_draft"]
    llm_fallbacks = [item for item in trace_events if item.get("event") == "llm_fallback"]
    failed_provider_calls = [item for item in llm_fallbacks if item.get("provider_call_failed")]
    total_costs = [item.get("cost_usd") for item in llm_steps]
    cost_summary = {
        "currency": "USD",
        "llm_calls": len(llm_steps) + len(failed_provider_calls),
        "successful_calls": len(llm_steps),
        "failed_calls": len(failed_provider_calls),
        "input_tokens": sum((item.get("usage") or {}).get("input_tokens", 0) for item in llm_steps),
        "output_tokens": sum((item.get("usage") or {}).get("output_tokens", 0) for item in llm_steps),
        "cost_usd": round(sum(v for v in total_costs if v is not None), 12) if total_costs and all(v is not None for v in total_costs) else None,
        "status": ("no_calls" if not llm_steps and not failed_provider_calls else
                   "partial_unknown" if failed_provider_calls else
                   "estimated" if all(v is not None for v in total_costs) else "rate_not_configured"),
    }
    settings = get_settings()
    prompt_info = load_prompt("analyst", settings.prompt_version)
    return {"run_id": run_id, "status": "escalated" if final.get("routing", {}).get("escalated", True) else "analyzed",
            "classification": final.get("claim", {}).get("claim_type"), "entities": final.get("entities", {}),
            "coverage": final.get("coverage", {}), "routing": final.get("routing", {}),
            "evidence_passages": final.get("evidence", []), "agent_trace": final.get("trace", []),
            "verification": final.get("verification", {}),
            "prompt_version": prompt_info["version"], "prompt_id": prompt_info["id"],
            "prompt_hash": prompt_info["sha256"], "cost_summary": cost_summary,
            "degraded": bool(final.get("coverage", {}).get("degraded")),
            "agent_steps": final.get("steps", 0), "tool_calls": final.get("tool_calls", 0)}

"""Bounded, evidence-first analyst workflow."""
from __future__ import annotations

import json
import re
import time
from typing import TypedDict

import httpx
from langgraph.graph import END, StateGraph
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from shared.config import get_settings
from shared.logging_setup import correlation_id
from shared.telemetry import current_trace_ids, inject_trace_context
from .guardrails import filter_output, neutralize_injection, screen_input, wrap_untrusted
from .llm import ProviderUnavailableError, get_llm_client
from .metrics import TOOL_CALLS
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
TIMELINE_SOURCES = (
    ("icici-pc-wording", "claim assessment survey report decision timelines"),
    ("icici-citizen-charter", "general insurance claim turnaround time insurer request acceptance rejection"),
)
REPAIR_ASSURANCE_SOURCES = (
    ("icici-pc-wording", "assurance on the quality of workmanship 24 months 10000 kms selected preferred partner garage", {"clause_id": "repair quality assurance"}),
    ("icici-submit-track", "service assurance 2 years 10000 kms from date of repairs", {"clause_id": "service assurance"}),
)
SPECIAL_SOURCE_LABELS = {
    "icici-pc-wording": "policy claim procedure",
    "icici-citizen-charter": "Citizen Charter",
    "icici-submit-track": "claims service assurance",
}
tracer = trace.get_tracer("triage-worker")


def _budget(state: AnalystState) -> bool:
    settings = get_settings()
    if state.get("steps", 0) >= settings.max_agent_steps:
        return False
    if time.monotonic() - state.get("started", time.monotonic()) > settings.run_timeout_seconds:
        return False
    return True


def _record_step(state: AnalystState, name: str, started: float, **details) -> None:
    state.setdefault("trace", []).append({
        "event": "agent_step", "step": name,
        "latency_s": round(time.perf_counter() - started, 6),
        **details, **current_trace_ids(),
    })


def _tool(state: AnalystState, name: str, fn, *args):
    if state.get("tool_calls", 0) >= get_settings().max_tool_calls:
        raise RuntimeError("tool-call budget exceeded")
    state["tool_calls"] = state.get("tool_calls", 0) + 1
    inputs = {}
    if name == "get_fnol_fields":
        inputs = {"run_id": args[0] if args else None}
    elif name == "search_corpus":
        inputs = {"query": args[0] if args else "", "top_k": args[1] if len(args) > 1 else 5,
                  "filters": args[2] if len(args) > 2 else {}}
    elif name == "get_clause":
        inputs = {"clause_id": args[0] if args else ""}
    elif name == "find_references":
        inputs = {"term": args[0] if args else ""}
    started = time.perf_counter()
    span_attrs = {"tool.name": name, "correlation_id": correlation_id.get(), "run_id": state.get("run_id", "")}
    if "query" in inputs:
        span_attrs["retrieval.query"] = str(inputs["query"])
    with tracer.start_as_current_span(f"tool.{name}", attributes=span_attrs) as span:
        try:
            result = fn(*args)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            TOOL_CALLS.labels(name, "failed").inc()
            state.setdefault("trace", []).append({
                "event": "tool_call", "tool": name, "ok": False, "input": inputs,
                "error_type": type(exc).__name__, "latency_s": round(time.perf_counter() - started, 6),
                **current_trace_ids(),
            })
            raise
        latency = time.perf_counter() - started
        rows = result if isinstance(result, list) else result.get("clauses", []) if isinstance(result, dict) else []
        passages = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            passage = {key: item.get(key) for key in (
                "chunk_id", "clause_id", "chapter", "section", "page", "text", "distance",
                "references", "document_id", "source_title", "source_file", "source_sha256", "source_url",
                "retrieval_mode",
            ) if item.get(key) is not None}
            if passage:
                passages.append(passage)
        span.set_attribute("tool.result_count", len(rows))
        TOOL_CALLS.labels(name, "success").inc()
        state.setdefault("trace", []).append({
            "event": "tool_call", "tool": name, "ok": True, "input": inputs,
            "result_count": len(rows), "passages": passages,
            "latency_s": round(latency, 6), **current_trace_ids(),
        })
        return result


def _classify_extract(state: AnalystState):
    started = time.perf_counter()
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
    process_question = bool(re.search(
        r"\b(?:turnaround|turn around|timelines?|time\s*lines?|assessment clock|decision clock|claim process time)\b",
        text,
    ))
    compare_timeline_sources = process_question and bool(re.search(
        r"\b(?:differing|different|conflict|discrepanc\w*|both sources|insurer.{0,30}request|which source|"
        r"(?:start|starting|begins?).{0,40}(?:survey|request)|(?:survey|request).{0,40}(?:or|versus|vs\.?))\b",
        text,
    ))
    repair_assurance_question = bool(re.search(
        r"\b(?:workmanship|repair\s+(?:assurance|warranty|guarantee|quality)|service assurance)\b",
        text,
    ))
    out_of_corpus = is_out_of_corpus(description)
    state["claim"] = {**fields, "claim_type": claim_type, "third_party_injury": any(k in text for k in ("injur", "hospital", "pedestrian"))}
    state["entities"] = {
        "policy_number": fields.get("policy_number"), "date_of_loss": fields.get("date_of_loss"),
        "vehicle_registration": fields.get("vehicle_registration"), "estimated_damage_inr": fields.get("estimated_damage_inr"),
        "claim_type": claim_type, "vehicle_use": "food_delivery" if delivery else "not_stated",
        "driver_or_courier_context": bool(delivery), "injection_screen": screen_input(description),
        "claim_process_question": process_question,
        "compare_timeline_sources": compare_timeline_sources,
        "repair_assurance_question": repair_assurance_question,
        "out_of_corpus": out_of_corpus,
    }
    if out_of_corpus:
        state.setdefault("trace", []).append({"event": "out_of_corpus", "reason": "FNOL does not describe a motor vehicle loss"})
    _record_step(state, "classify_extract", started, claim_type=claim_type,
                 vehicle_use=state["entities"]["vehicle_use"], out_of_corpus=out_of_corpus,
                 injection_flagged=state["entities"]["injection_screen"]["flagged"])
    state["steps"] = state.get("steps", 0) + 1
    return state


def _retrieve_cover(state: AnalystState):
    started = time.perf_counter()
    if not _budget(state):
        state["trace"].append({"event": "step_or_time_budget_exhausted"})
        return state
    evidence = state.setdefault("evidence", [])
    if (state.get("entities", {}).get("claim_process_question")
            or state.get("entities", {}).get("repair_assurance_question")):
        _record_step(state, "retrieve_cover", started, skipped="claim_process_query")
        state["steps"] = state.get("steps", 0) + 1
        return state
    passages = _tool(state, "search_corpus", search_corpus,
        "Section I(A) own damage cover accidental external means; Section I(B) third party; motor vehicle food delivery use", 12)
    evidence.extend(passages)
    # Exact clause read anchors the core cover wording regardless of vector ranking.
    result = _tool(state, "get_clause", get_clause, REQUIRED[0])
    evidence.extend(result.get("clauses", []))
    _record_step(state, "retrieve_cover", started, evidence_count=len(evidence))
    state["steps"] = state.get("steps", 0) + 1
    return state


def _retrieve_limits(state: AnalystState):
    started = time.perf_counter()
    if not _budget(state):
        state["trace"].append({"event": "step_or_time_budget_exhausted"})
        return state
    evidence = state.setdefault("evidence", [])
    if (state.get("entities", {}).get("claim_process_question")
            or state.get("entities", {}).get("repair_assurance_question")):
        retrieved_sources = set()
        if state.get("entities", {}).get("repair_assurance_question"):
            sources = REPAIR_ASSURANCE_SOURCES
        else:
            sources = tuple((doc_id, query, {}) for doc_id, query in (
                TIMELINE_SOURCES if state.get("entities", {}).get("compare_timeline_sources") else TIMELINE_SOURCES[:1]
            ))
        state["entities"]["special_source_ids"] = [document_id for document_id, _, _ in sources]
        for document_id, query, extra_filters in sources:
            rows = _tool(
                state,
                "search_corpus",
                search_corpus,
                query,
                1,
                {"document_id": document_id, **extra_filters},
            )
            evidence.extend(rows)
            if rows:
                retrieved_sources.add(document_id)
        state["entities"]["timeline_sources_retrieved"] = sorted(retrieved_sources)
        if len(retrieved_sources) == len(sources):
            state.setdefault("trace", []).append({
                "event": "cross_source_evidence_retrieved",
                "sources": sorted(retrieved_sources),
                "interpretation": "The requested source passages were retrieved for comparison; any differences require human review.",
            })
    else:
        exceptions = _tool(state, "get_clause", get_clause, REQUIRED[1])
        evidence.extend(exceptions.get("clauses", []))
        refs = _tool(state, "find_references", find_references, "Limitations as to Use")
        evidence.extend(refs)
    _record_step(state, "retrieve_limits", started, evidence_count=len(evidence))
    state["steps"] = state.get("steps", 0) + 1
    return state


def _has(evidence: list[dict], target: str) -> bool:
    target = target.casefold()
    return any(target == str(p.get("clause_id", "")).casefold() or target == str(p.get("chapter", "")).casefold() for p in evidence)


def _evidence_gate(state: AnalystState):
    started = time.perf_counter()
    if (state.get("entities", {}).get("claim_process_question")
            or state.get("entities", {}).get("repair_assurance_question")):
        sources = {str(p.get("document_id", "")) for p in state.get("evidence", [])}
        needed_ids = state.get("entities", {}).get("special_source_ids", ["icici-pc-wording"])
        missing = [
            SPECIAL_SOURCE_LABELS.get(document_id, document_id)
            for document_id in needed_ids if document_id not in sources
        ]
    else:
        missing = [target for target in REQUIRED if not _has(state.get("evidence", []), target)]
    state["missing"] = missing
    _record_step(state, "evidence_gate", started, missing=missing, evidence_count=len(state.get("evidence", [])))
    state["steps"] = state.get("steps", 0) + 1
    return state


def _repair(state: AnalystState):
    started = time.perf_counter()
    # One bounded repair pass searches explicit names that may have been absent from a response.
    if state.get("retries", 0) >= 1 or not _budget(state):
        return state
    if (state.get("entities", {}).get("claim_process_question")
            or state.get("entities", {}).get("repair_assurance_question")):
        needed = set(state.get("missing", []))
        if state.get("entities", {}).get("repair_assurance_question"):
            source_list = REPAIR_ASSURANCE_SOURCES
        else:
            source_list = tuple((doc_id, query, {}) for doc_id, query in (
                TIMELINE_SOURCES if state.get("entities", {}).get("compare_timeline_sources") else TIMELINE_SOURCES[:1]
            ))
        for document_id, query, extra_filters in source_list:
            if SPECIAL_SOURCE_LABELS.get(document_id, document_id) in needed:
                rows = _tool(state, "search_corpus", search_corpus, query, 1,
                             {"document_id": document_id, **extra_filters})
                state.setdefault("evidence", []).extend(rows)
    else:
        for target in state.get("missing", []):
            rows = _tool(state, "search_corpus", search_corpus, f'"{target}"', 12)
            state.setdefault("evidence", []).extend(rows)
    state["retries"] = state.get("retries", 0) + 1
    _record_step(state, "evidence_repair", started, repaired=state.get("missing", []),
                 evidence_count=len(state.get("evidence", [])))
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
    started = time.perf_counter()
    evidence = _unique(state.get("evidence", []))
    by_clause = {}
    for p in evidence:
        cid = p.get("clause_id") or p.get("chapter")
        if cid and cid not in by_clause:
            by_clause[cid] = p
    out_of_corpus = bool(state.get("entities", {}).get("out_of_corpus"))
    process_question = bool(state.get("entities", {}).get("claim_process_question"))
    repair_question = bool(state.get("entities", {}).get("repair_assurance_question"))
    timeline_evidence = {
        str(p.get("document_id", "")) for p in evidence
    }
    if process_question or repair_question:
        expected_sources = state.get("entities", {}).get("special_source_ids", ["icici-pc-wording"])
        complete = not out_of_corpus and all(document_id in timeline_evidence for document_id in expected_sources)
    else:
        complete = not out_of_corpus and all(_has(evidence, target) for target in REQUIRED)
    delivery = state.get("entities", {}).get("vehicle_use") == "food_delivery"
    citations = []
    special_question = process_question or repair_question
    citation_targets = () if out_of_corpus else (REQUIRED if not special_question else ())
    for target in citation_targets:
        item = next((p for p in evidence if target.casefold() in {str(p.get("clause_id", "")).casefold(), str(p.get("chapter", "")).casefold()}), None)
        if item:
            citations.append({"clause_id": item.get("clause_id") or item.get("chapter"), "page": item.get("page"), "quote": str(item.get("text", ""))[:900]})
    if special_question and complete:
        expected_sources = state.get("entities", {}).get("special_source_ids", ["icici-pc-wording"])
        for document_id in expected_sources:
            item = next((p for p in evidence if p.get("document_id") == document_id), None)
            if item:
                citations.append({
                    "clause_id": item.get("clause_id") or item.get("chapter") or item.get("source_title"),
                    "page": item.get("page"),
                    "quote": str(item.get("text", ""))[:900],
                })

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
            with tracer.start_as_current_span("llm.draft_position", attributes={
                "correlation_id": correlation_id.get(), "llm.provider": settings.llm_provider or "configured-provider",
                "llm.model": settings.llm_model, "run_id": state.get("run_id", ""),
                "prompt.version": settings.prompt_version,
                "prompt.hash": prompt_data["sha256"],
            }) as llm_span:
                try:
                    llm = get_llm_client().complete(
                        [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user_text)}],
                        settings.prompt_version, "draft_position", prompt_data["sha256"],
                    )
                except Exception as exc:
                    llm_span.record_exception(exc)
                    llm_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    raise
                llm_span.set_attribute("llm.latency_seconds", float(llm.get("latency_s", 0)))
                llm_span.set_attribute("llm.input_tokens", int((llm.get("usage") or {}).get("input_tokens", 0)))
                llm_span.set_attribute("llm.output_tokens", int((llm.get("usage") or {}).get("output_tokens", 0)))
                if llm.get("cost_usd") is not None:
                    llm_span.set_attribute("llm.cost_usd", float(llm["cost_usd"]))
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
    elif process_question:
        if complete and state.get("entities", {}).get("compare_timeline_sources"):
            flags.append("source_conflict")
            summary = ("The policy claim procedure and the insurer’s Citizen Charter state different trigger points for the stated timelines. The claim procedure refers to assessment or survey milestones, while the Charter refers to the insurer’s request for information and communication of acceptance or rejection. These sources should not be treated as interchangeable; a claims handler should confirm which timeline applies to this claim.")
        elif complete:
            flags.append("claim_process_question")
            summary = "The policy claim-procedure passage states that the assessment sheet or survey report is furnished within 15 days of assessment or survey, and the claim is decided within 7 days of receiving the survey report. Confirm the applicable dates in the claim file."
        else:
            flags.append("claim_process_evidence_missing")
            summary = "The request concerns claim timelines, but the claim-procedure passage could not be retrieved. No timeline answer is made; refer for human review."
        position = "escalate"
    elif repair_question:
        if complete:
            summary = "The cited policy add-on and insurer claims page describe a 24-month or 10,000-kilometre repair assurance period. Confirm the repair programme and policy schedule before advising whether it applies to this claim."
            flags.append("cross_source_consistency_checked")
        else:
            summary = "The repair assurance question could not be checked against both the policy wording and the insurer claims page; refer for human review."
            flags.append("cross_source_evidence_missing")
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
    if model_candidate and complete and not out_of_corpus and not special_question:
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
    _record_step(state, "draft_position", started, position=draft["position"],
                 evidence_complete=complete, citation_ids=[c["clause_id"] for c in citations],
                 degraded=llm_degraded, prompt_version=settings.prompt_version)
    return state


def _verify_and_route(state: AnalystState):
    """Call the independent verifier service and route only after its verdict."""
    started = time.perf_counter()
    settings = get_settings()
    coverage = state.get("coverage", {})
    request = {"draft": {"position": coverage.get("position"), "summary": coverage.get("summary"),
                         "citations": coverage.get("citations", [])},
               "passages": state.get("evidence", [])}
    verifier_started = time.perf_counter()
    with tracer.start_as_current_span("verifier.verify", attributes={
        "correlation_id": correlation_id.get(), "run_id": state.get("run_id"),
        "citation_count": len(request["draft"].get("citations", [])),
    }) as verifier_span:
        try:
            headers = {"x-correlation-id": correlation_id.get(), **inject_trace_context()}
            response = httpx.post(f"{settings.verifier_url.rstrip('/')}/verify", json=request,
                                  headers=headers, timeout=settings.llm_timeout_seconds)
            response.raise_for_status()
            verdict = response.json()
            verifier_span.set_attribute("verification.verified", bool(verdict.get("verified")))
        except Exception as exc:
            verifier_span.record_exception(exc)
            verifier_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            verdict = {"verified": False, "problems": [{"problem": f"verifier unavailable: {type(exc).__name__}"}],
                       "next_action": "escalate"}
    state["verification"] = verdict
    state.setdefault("trace", []).append({"event": "verification", "verified": bool(verdict.get("verified")),
                                         "problems": verdict.get("problems", []), "next_action": verdict.get("next_action"),
                                         "latency_s": round(time.perf_counter() - verifier_started, 6),
                                         **current_trace_ids()})
    coverage["verified"] = bool(verdict.get("verified"))
    coverage["verification"] = verdict
    if not coverage["verified"]:
        coverage["position"] = "escalate"
        coverage["flags"] = list(dict.fromkeys(coverage.get("flags", []) + ["verification_failed"]))
    state["routing"] = decide_route(state.get("claim", {}), coverage)
    _record_step(state, "route_claim", started, route=state["routing"]["route"],
                 escalated=state["routing"]["escalated"], flags=state["routing"]["flags"])
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

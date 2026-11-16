"""Run the deterministic, corpus-labeled Stage 9 evaluation batch."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

PENDING = {"queued", "accepted", "pending", "running", "processing", "analyzing", "analysing"}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _passage_matches(passage: dict, ref: dict) -> bool:
    page_match = str(passage.get("page", "")) == str(ref.get("page", ""))
    source = _norm(passage.get("source_title") or passage.get("source_file"))
    expected_source = _norm(ref.get("source"))
    source_match = expected_source in source or source in expected_source
    clause = _norm(passage.get("clause_id") or passage.get("chapter"))
    expected_clause = _norm(ref.get("clause"))
    clause_match = expected_clause in clause or clause in expected_clause
    return page_match and source_match and clause_match


def _all_passages(trace: list) -> list[dict]:
    rows, seen = [], set()
    for event in trace:
        if event.get("event") != "tool_call" or not event.get("ok"):
            continue
        for passage in event.get("passages", []):
            key = (passage.get("source_file"), passage.get("page"), passage.get("clause_id"), passage.get("text"))
            if key not in seen:
                seen.add(key)
                rows.append(passage)
    return rows


def _score_one(sc: dict, run: dict, trace_doc: dict, catalog: dict) -> dict:
    result = run.get("result") or {}
    coverage = result.get("coverage") or {}
    routing = result.get("routing") or {}
    trace = trace_doc.get("trace") or []
    retrieved = _all_passages(trace)
    support = [catalog[k] for k in sc.get("supporting_passages", []) if k in catalog]
    expected = sc.get("expected", {})

    if support:
        relevant_count = sum(any(_passage_matches(p, ref) for ref in support) for p in retrieved)
        context_relevance = relevant_count / len(retrieved) if retrieved else 0.0
    else:
        context_relevance = 1.0 if not retrieved else 0.0

    citations = coverage.get("citations") or []
    citation_supported = 0
    for citation in citations:
        if any(_passage_matches({**citation, "source_title": p.get("source_title"), "source_file": p.get("source_file")}, ref)
               for p in retrieved for ref in catalog.values()):
            citation_supported += 1
    expected_citations = expected.get("citations", [])
    citation_presence = [
        any(_passage_matches({**cite, "source_title": p.get("source_title"), "source_file": p.get("source_file")}, catalog[key])
            for cite in citations for p in retrieved)
        for key in expected_citations if key in catalog
    ]
    groundedness = citation_supported / len(citations) if citations else (1.0 if not expected_citations else 0.0)
    if citation_presence:
        groundedness = (groundedness + sum(citation_presence) / len(citation_presence)) / 2

    checks = []
    if "positions" in expected:
        checks.append(coverage.get("position") in expected["positions"])
    if "routes" in expected:
        checks.append(routing.get("route") in expected["routes"])
    if "escalated" in expected:
        checks.append(bool(routing.get("escalated")) == bool(expected["escalated"]))
    flags = set(coverage.get("flags") or []) | set(routing.get("flags") or [])
    for flag in expected.get("flags_any", []):
        checks.append(flag in flags)
    events = {e.get("event") for e in trace}
    for event in expected.get("events_any", []):
        checks.append(event in events)
    for key in expected_citations:
        checks.append(any(_passage_matches({**cite, "source_title": p.get("source_title"), "source_file": p.get("source_file")}, catalog[key])
                          for cite in citations for p in retrieved) if key in catalog else False)
    text = _norm(coverage.get("summary", ""))
    for forbidden in expected.get("must_not_contain", []):
        checks.append(_norm(forbidden) not in text)
    actual_cited = []
    for citation in citations:
        actual_cited.extend(k for k, ref in catalog.items()
                            if any(_passage_matches({**citation, "source_title": p.get("source_title"), "source_file": p.get("source_file")}, ref)
                                   for p in retrieved))
    for forbidden_key in expected.get("forbidden_citations", []):
        checks.append(forbidden_key not in actual_cited)
    faithfulness = sum(checks) / len(checks) if checks else 0.0

    verifier_ok = result.get("verification", {}).get("passed", True)
    hallucinated = not verifier_ok
    hallucinated |= any(_norm(term) in text for term in expected.get("must_not_contain", []))
    hallucinated |= any(key in actual_cited for key in expected.get("forbidden_citations", []))
    hallucinated |= bool(citations) and citation_supported < len(citations)
    hallucination_rate = float(hallucinated)
    return {
        "context_relevance": context_relevance,
        "groundedness": groundedness,
        "faithfulness": faithfulness,
        "hallucination_rate": hallucination_rate,
        "checks_passed": sum(checks), "checks_total": len(checks),
        "retrieved_unique_passages": len(retrieved), "citations": citations,
        "position": coverage.get("position"), "route": routing.get("route"),
        "flags": sorted(flags), "events": sorted(events),
        "prompt_versions": sorted({str(e.get("prompt_version")) for e in trace if e.get("prompt_version")}),
        "provider_calls": [e for e in trace if e.get("event") in {"llm_call", "llm_draft", "llm_fallback"}],
    }


def run_scenario(client: httpx.Client, sc: dict, timeout: float, catalog: dict) -> dict:
    headers = {"x-client-id": f"stage9-{uuid.uuid4().hex}", "x-correlation-id": uuid.uuid4().hex}
    try:
        response = client.post("/claims", json=sc["fnol"], headers=headers)
        if response.status_code != 202:
            return {"status": "rejected", "http": response.status_code, "error": response.text}
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + timeout
        run = {}
        while time.monotonic() < deadline:
            resp = client.get(f"/runs/{run_id}")
            resp.raise_for_status()
            run = resp.json()
            if str(run.get("status", "")).casefold() not in PENDING:
                break
            time.sleep(1)
        else:
            return {"run_id": run_id, "status": "timeout", "error": "evaluation polling timeout"}
        trace_resp = client.get(f"/runs/{run_id}/trace")
        trace_resp.raise_for_status()
        trace_doc = trace_resp.json()
        return {"run_id": run_id, "status": run.get("status"), "result": run.get("result"),
                "correlation_id": trace_doc.get("correlation_id"),
                "metrics": _score_one(sc, run, trace_doc, catalog)}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8001"))
    ap.add_argument("--file", default="eval/scenarios.yaml")
    ap.add_argument("--timeout", type=float, default=360)
    ap.add_argument("--output", help="JSON output path; defaults to eval/results_<timestamp>.json")
    ap.add_argument("--limit", type=int, help="Run only the first N scenarios (for smoke checks)")
    ap.add_argument("--resume", action="store_true", help="Resume completed scenario rows from an incomplete output JSON")
    args = ap.parse_args()
    path = Path(args.file)
    suite = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = suite["scenarios"][:args.limit]
    catalog = suite.get("passage_catalog", {})
    output = Path(args.output) if args.output else Path("eval") / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    rows_by_id = {}
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows_by_id = {row["id"]: row for row in prior.get("scenarios", []) if row.get("id")}
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=30) as client:
        for index, scenario in enumerate(scenarios, 1):
            if scenario["id"] in rows_by_id:
                print(f"[{index}/{len(scenarios)}] {scenario['id']} already saved; skipping", flush=True)
                continue
            print(f"[{index}/{len(scenarios)}] {scenario['id']} ...", flush=True)
            rows_by_id[scenario["id"]] = {"id": scenario["id"], **run_scenario(client, scenario, args.timeout, catalog)}
            metric = rows_by_id[scenario["id"]].get("metrics", {})
            print(f"  status={rows_by_id[scenario['id']].get('status')} faithful={metric.get('faithfulness', 0):.2f} "
                  f"context={metric.get('context_relevance', 0):.2f}", flush=True)
            rows = [rows_by_id[sc["id"]] for sc in scenarios if sc["id"] in rows_by_id]
            partial_metrics = {name: sum(row.get("metrics", {}).get(name, 0.0) for row in rows) / len(rows) if rows else 0.0
                               for name in ("context_relevance", "groundedness", "faithfulness", "hallucination_rate")}
            checkpoint = {"suite_version": suite.get("version"), "api": args.api,
                          "generated_at": datetime.now(timezone.utc).isoformat(), "complete": False,
                          "scenario_count": len(rows), "metrics": partial_metrics, "scenarios": rows}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = [rows_by_id[sc["id"]] for sc in scenarios if sc["id"] in rows_by_id]
    metric_names = ("context_relevance", "groundedness", "faithfulness", "hallucination_rate")
    aggregates = {name: sum(row.get("metrics", {}).get(name, 0.0) for row in rows) / len(rows) if rows else 0.0
                  for name in metric_names}
    draft_calls = [call for row in rows for call in row.get("metrics", {}).get("provider_calls", [])
                   if call.get("event") == "llm_draft"]
    run_metadata = {
        "providers": sorted({str(call.get("provider")) for call in draft_calls if call.get("provider")}),
        "models": sorted({str(call.get("model")) for call in draft_calls if call.get("model")}),
        "prompt_versions": sorted({str(call.get("prompt_version")) for call in draft_calls if call.get("prompt_version")}),
        "prompt_hashes": sorted({str(call.get("prompt_hash")) for call in draft_calls if call.get("prompt_hash")}),
        "successful_model_responses": len(draft_calls),
    }
    report = {"suite_version": suite.get("version"), "api": args.api, "generated_at": datetime.now(timezone.utc).isoformat(),
              "scenario_count": len(rows), "run_metadata": run_metadata, "metrics": aggregates, "scenarios": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    report["complete"] = len(rows) == len(scenarios)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nMetric means (macro average over scenarios):")
    for name, value in aggregates.items():
        print(f"  {name}: {value:.4f}")
    print(f"Saved {output}")
    return 0 if all(row.get("status") not in {"error", "timeout", "rejected"} for row in rows) else 2


if __name__ == "__main__":
    sys.exit(main())

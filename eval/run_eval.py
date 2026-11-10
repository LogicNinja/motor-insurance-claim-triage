"""Run the evaluation set as a batch from the command line (L3).

TODO(Stage 9): compute groundedness, context relevance, faithfulness and hallucination rate
with TruLens (or equivalent) and state how each is computed. Record the prompt version per run.
"""
import argparse
import os
import sys
import time

import httpx
import yaml

TERMINAL = {"completed", "escalated", "degraded", "failed"}


def run_scenario(client: httpx.Client, scenario: dict, timeout: float) -> dict:
    r = client.post("/claims", json=scenario["fnol"])
    if r.status_code != 202:
        return {"status": "rejected", "http": r.status_code, "body": r.text}
    run_id = r.json()["run_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/runs/{run_id}").json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(1)
    return {"status": "timeout", "run_id": run_id}


def check(scenario: dict, run: dict) -> list:
    exp = scenario.get("expected", {})
    result = run.get("result") or {}
    cov, routing = result.get("coverage", {}), result.get("routing", {})
    problems = []
    if "position" in exp and cov.get("position") != exp["position"]:
        problems.append(f"position {cov.get('position')} != {exp['position']}")
    if "route" in exp and routing.get("route") != exp["route"]:
        problems.append(f"route {routing.get('route')} != {exp['route']}")
    if "must_escalate" in exp and bool(routing.get("escalated")) != exp["must_escalate"]:
        problems.append(f"escalated {routing.get('escalated')} != {exp['must_escalate']}")
    cited = " | ".join(str(c.get("clause_id", "")) + " " + str(c.get("page", "")) for c in cov.get("citations", []))
    for want in exp.get("citations", []):
        if str(want["page"]) not in cited:
            problems.append(f"missing citation on page {want['page']}: {want['label']}")
    text = str(cov.get("summary", "")).lower()
    for bad in exp.get("must_not_contain", []):
        if bad.lower() in text:
            problems.append(f"forbidden text present: {bad}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--file", default="eval/scenarios.yaml")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    scenarios = yaml.safe_load(open(args.file, encoding="utf-8"))["scenarios"]
    failed = 0
    with httpx.Client(base_url=args.api, timeout=30) as client:
        for sc in scenarios:
            run = run_scenario(client, sc, args.timeout)
            problems = check(sc, run)
            failed += bool(problems)
            print(f"{'PASS' if not problems else 'FAIL'}  {sc['id']}  {'; '.join(problems)}")
    print(f"{len(scenarios) - failed}/{len(scenarios)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

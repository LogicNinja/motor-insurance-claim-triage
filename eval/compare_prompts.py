"""Compare two complete Stage 9 runs made with different analyst prompt versions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

METRICS = ("groundedness", "context_relevance", "faithfulness", "hallucination_rate")
HIGHER_IS_BETTER = {"groundedness", "context_relevance", "faithfulness"}


def _read(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("complete"):
        raise ValueError(f"{path} is not a complete evaluation run")
    if not data.get("scenarios"):
        raise ValueError(f"{path} contains no scenario rows")
    failures = [row.get("id") for row in data["scenarios"]
                if row.get("status") in {"error", "timeout", "rejected"}]
    if failures:
        raise ValueError(f"{path} has unsuccessful scenarios: {failures}")
    return data


def _scenario_versions(run: dict) -> set[str]:
    versions = set()
    for row in run["scenarios"]:
        result = (row.get("result") or {})
        coverage = result.get("coverage") or {}
        if coverage.get("prompt_version"):
            versions.add(str(coverage["prompt_version"]))
    return versions


def compare(before: dict, after: dict) -> dict:
    if before.get("suite_version") != after.get("suite_version"):
        raise ValueError("The runs use different evaluation suite versions")
    before_ids = [row.get("id") for row in before["scenarios"]]
    after_ids = [row.get("id") for row in after["scenarios"]]
    if before_ids != after_ids:
        raise ValueError("The runs do not contain the same scenarios in the same order")

    before_meta = before.get("run_metadata", {})
    after_meta = after.get("run_metadata", {})
    for field in ("providers", "models"):
        if before_meta.get(field) != after_meta.get(field):
            raise ValueError(f"The runs differ in {field}: {before_meta.get(field)} vs {after_meta.get(field)}")

    before_versions = _scenario_versions(before)
    after_versions = _scenario_versions(after)
    if len(before_versions) != 1 or len(after_versions) != 1 or before_versions == after_versions:
        raise ValueError(f"Expected one distinct prompt version per run; got {before_versions} and {after_versions}")

    metrics = {}
    for name in METRICS:
        old = float(before["metrics"][name])
        new = float(after["metrics"][name])
        delta = round(new - old, 6)
        if delta == 0:
            winner = "tie"
        else:
            improved = delta > 0 if name in HIGHER_IS_BETTER else delta < 0
            winner = "after" if improved else "before"
        metrics[name] = {"before": old, "after": new, "change": delta, "better": winner}

    paired_rows = []
    for old, new in zip(before["scenarios"], after["scenarios"]):
        old_metrics = old.get("metrics", {})
        new_metrics = new.get("metrics", {})
        paired_rows.append({
            "id": old["id"],
            "before_run_id": old.get("run_id"),
            "after_run_id": new.get("run_id"),
            "before_status": old.get("status"),
            "after_status": new.get("status"),
            "metrics": {
                name: {
                    "before": old_metrics.get(name),
                    "after": new_metrics.get(name),
                    "change": round(float(new_metrics.get(name, 0)) - float(old_metrics.get(name, 0)), 6),
                }
                for name in METRICS
            },
        })

    return {
        "suite_version": before.get("suite_version"),
        "scenario_count": len(before_ids),
        "provider": before_meta.get("providers"),
        "model": before_meta.get("models"),
        "prompt_before": sorted(before_versions),
        "prompt_after": sorted(after_versions),
        "prompt_hashes_before": before_meta.get("prompt_hashes", []),
        "prompt_hashes_after": after_meta.get("prompt_hashes", []),
        "metrics": metrics,
        "scenarios": paired_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="Complete results JSON for the first prompt")
    parser.add_argument("after", type=Path, help="Complete results JSON for the second prompt")
    parser.add_argument("--output", type=Path, default=Path("eval/prompt_comparison.json"))
    args = parser.parse_args()

    report = compare(_read(args.before), _read(args.after))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Compared {report['scenario_count']} scenarios: {report['prompt_before'][0]} -> {report['prompt_after'][0]}")
    for name, result in report["metrics"].items():
        print(f"{name}: {result['before']:.4f} -> {result['after']:.4f} "
              f"(change {result['change']:+.4f}; better: {result['better']})")
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

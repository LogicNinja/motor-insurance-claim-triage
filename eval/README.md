# Stage 9 evaluation

Run from the repository root with the API and worker available:

```powershell
python eval/run_eval.py --api http://localhost:8001 --output eval/results_baseline.json
```

The checked-in suite has 24 scenarios. It covers a food-delivery use restriction, ordinary accidental damage, theft and injury routing, out-of-corpus and non-motor claims, prompt injection, source conflicts (claim turnaround timelines and repair assurance), and distractor wording from policy summaries, purchase-document guidance, and a goods-vehicle endorsement. `passage_catalog` records the labeled corpus sources, pages, clause names and short identifying excerpts; every scenario names its relevant `supporting_passages` and any known `distractors`.

## Four metrics and calculation

The evaluator is deterministic and uses the persisted worker trace and final result. It makes no LLM-judge calls and adds no external evaluation service.

* **Context relevance** is per-scenario retrieval precision: unique retrieved passage records matching any labeled supporting passage by source, page, and clause, divided by all unique retrieved passage records. A scenario whose gold set is intentionally empty scores 1 only when retrieval returns no passages. The reported value is the unweighted mean across scenarios.
* **Groundedness** starts with the fraction of final citations whose source/page/clause can be found among passages in that run's tool trace. If the scenario has gold required citations, the score is the mean of that fraction and required-citation recall. If no citations are expected and none are produced, it is 1; otherwise no citations when required scores 0. The batch value is the unweighted mean.
* **Faithfulness** is the fraction of scenario oracle checks satisfied. Checks include allowed position and route, expected escalation, at-least-one required flag/event, required gold citations, forbidden summary phrases, and forbidden distractor citations, only when the scenario specifies each check. The batch value is the unweighted mean of per-scenario fractions.
* **Hallucination rate** is the fraction of scenarios with any rule-based failure: verifier not passed, a forbidden phrase in the position summary, a forbidden distractor cited, or a final citation that cannot be aligned to any passage in the run trace. Lower is better. This lexical/provenance proxy is interpretable but does not detect every semantic hallucination.

Passage matching is exact on page plus normalized source and clause labels (allowing one label to contain the other); it does not treat semantic similarity as proof. Repeated identical passage records in a trace are deduplicated. All four aggregate metrics are macro means, so each scenario has equal weight. The JSON output preserves each run ID, correlation ID, result, raw per-scenario values, flags and citations for inspection. `--limit N` is available for a connectivity smoke check; acceptance runs should omit it.

## Before/after protocol

Run the full suite, make one documented behavior/configuration change, and rerun the same suite with the same provider, model, prompt version, corpus and scenario file. Save outputs separately and compare all four macro metrics. This repository's run report is `report.md`; regenerate its values only from the two JSON result files. LLM outputs can vary between runs, so record provider/model/prompt metadata and treat small metric changes as potentially stochastic.

Source-conflict scenarios are deliberately included as evaluation probes. A scenario naming two sources in its gold set tests retrieval of both; it does not imply the current analyst has a dedicated conflict-resolution feature. The expected outputs permit cautious positions/routes while requiring citations and prohibit false claims that differing timeline start points are identical.

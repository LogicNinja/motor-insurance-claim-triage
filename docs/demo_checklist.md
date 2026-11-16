# Demo rehearsal checklist

Run from the repository root. For items that make claims, keep the returned run IDs so the audience
can inspect the same runs in the UI, API, trace, and evaluation output. Restore the stub failure
switch after item 8. The default stack is sufficient for items 1, 2, 4-8, and 10; item 3 requires
the optional `obs` profile, and item 9 requires Python packages from `requirements-dev.txt`.

| # | Demo item | Rehearsal procedure and evidence to show | Status |
|---|---|---|---|
| 1 | Clean startup and first-run index | Follow README “Run from a clean checkout”, including the one-time corpus build. Show healthy default services, `GET /health` returning `ok`, and the build result listing indexed documents and chunks. | Rehearsed 2026-11-16: isolated clean copy; 4 indexed documents, 447 chunks; all defaults healthy. |
| 2 | Valid claim, recommendation, citations | Submit through http://localhost:8080; wait for a terminal status; show recommendation, route, three citations, and the run ID. Open each citation with `GET /clause/{clause_id}`. | Rehearsed: clean-install run `9f8d3363c5854dbd85b392e9b674193b`, analyzed, 3 citations, surveyor route; smoke run `f9a3d4dedede4cdf86aaa52a346a8e27` also passed. |
| 3 | Full run trace and distributed trace | Enable telemetry and the `obs` profile as in [`observability.md`](observability.md). Submit a claim; show `/runs/{id}/trace`, its correlated Jaeger trace, Prometheus worker/API metrics, and the provisioned Grafana dashboard. | Rehearsed: run `8cb90e30a0c84ae18d6e14ce5d520515`, Jaeger trace `fe1ebce3c8ee68d928343d3d14443360`; Grafana dashboard and 4 Prometheus targets present. |
| 4 | Invalid request quarantined without a run | Submit `{"policy_number":"","date_of_loss":"bad"}` to `POST /claims`. Show HTTP 422 with `quarantine_id` and field errors. In `docker compose exec postgres psql -U triage -d triage`, query `SELECT id, errors FROM quarantine ORDER BY received_at DESC LIMIT 1;` and confirm no run was created for this request. | Rehearsed twice: quarantine row 9 and README smoke row 10; both matching correlation IDs had run count 0. |
| 5 | Out-of-corpus case escalates | Run the `out_of_corpus_mars` scenario using the full evaluation command below. Show its no-coverage position, `out_of_corpus` flag, and human-review route. | Rehearsed: scenario run `300e3be1b9e9481ea62454dc4480eecb`, escalated to human review. |
| 6 | Unsupported answer caught | Run the verifier tests with `python -m pytest -q tests/test_verifier.py`; show the deliberately unsupported citation is rejected and the result escalates or requests another retrieval. | Rehearsed: `tests/test_verifier.py`, 6 passed; invented quote and unknown clause rejected. |
| 7 | Prompt injection neutralized | Run `injection_ignore` and `injection_override` through the evaluation batch. Show `injection_neutralized` and `model_output_ignored_for_injection` in the traces; verify the injected instructions did not change the route or policy result. | Rehearsed: both scenarios passed; traces contain both neutralization events; run IDs `037c1a1606114de0af16f0626576b867` and `c94079b25ed54e8197fd727aeee4ad5b`. |
| 8 | Circuit breaker and safe fallback | Set stub failure rate to 1 using the first command below, submit enough claims to exceed the configured threshold, and show `circuit_breaker_open`, `degraded=true`, position `escalate`, and route `human_review`. Set the rate back to 0 and restart the worker. | Rehearsed: breaker opened on run `8126560b8c7b46f797e202bcf75742bb`; next run `c618d6d446b04e77bd5bec2100f95575` short-circuited. All outage results escalated; stub restored to 0. |
| 9 | SLO, failure concurrency, scale, protections | Follow the reproducible protocol and results in [`slo.md`](slo.md). Show the committed SLO date before load-test results, first failing concurrency, identified rate-limit bottleneck, `--scale` comparison, and measured cache/rate-limit effects. | Rehearsed from recorded Stage 10 results: first failure at 4 users under old quota; worker scaling did not remove limiter bottleneck; cache p95 3.2s → 2.7s; revised quota passed at 4 and limited at 8. Not rerun during this rehearsal. |
| 10 | Food-delivery case | Run scenario `food_delivery` in the evaluation batch. Show the food-delivery context, General Exception III(a), Chapter 4, citations, and human investigation/review route. | Rehearsed: scenario run `d32fec7bc0814fc89d0d78c5c5f29795`, analyzed with three required citations and investigation route; smoke run `f9a3d4dedede4cdf86aaa52a346a8e27` passed the same checks. |

## Commands used in the rehearsal

Run the full scenario batch from PowerShell after starting the default stack:

```powershell
python eval/run_eval.py --api http://localhost:8001 --output eval/demo_rehearsal.json
```

The JSON output records every scenario's run ID, result, trace, citations, and oracle checks. The
2026-11-16 rehearsal completed all 24 scenarios: context relevance 0.2269, groundedness 0.8542,
faithfulness 0.8340, hallucination rate 0.0000. Its output is `eval/demo_rehearsal.json`. To list
the four scenario identifiers used above, search `eval/scenarios.yaml` for `food_delivery`,
`out_of_corpus_mars`, `injection_ignore`, and `injection_override`.

Set and restore the stub's failure switch:

```powershell
docker compose exec stub-llm python -c "import urllib.request; r=urllib.request.Request('http://localhost:8000/admin/fail-rate/1', method='POST'); print(urllib.request.urlopen(r).read().decode())"
docker compose exec stub-llm python -c "import urllib.request; r=urllib.request.Request('http://localhost:8000/admin/fail-rate/0', method='POST'); print(urllib.request.urlopen(r).read().decode())"
docker compose restart worker
```

## Rehearsal record

After each rehearsal, replace its status with the date and evidence (run ID, evaluation scenario,
trace ID, or Locust result). Do not mark an item complete based only on code being present.

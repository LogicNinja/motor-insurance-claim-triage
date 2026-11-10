# Demo checklist

| # | Item | How to show it |
|---|------|----------------|
| 1 | `docker compose up` from a clean clone | Fresh clone, `cp .env.example .env`, `docker compose up --build` |
| 2 | Valid request, routed recommendation, citations resolve | Web client or `scripts/smoke.sh` |
| 3 | One run traced end to end | `GET /runs/{id}/trace`, plus Jaeger and Grafana |
| 4 | Malformed request rejected and quarantined, no run | Submit an invalid FNOL; show the 422 and the quarantine row; show no run row |
| 5 | Out-of-corpus case escalates | Scenario S03 |
| 6 | Verification catches an unsupported answer | Force a bad citation; show the search-again or escalation |
| 7 | Prompt injection and the guardrail response | Scenario S04 |
| 8 | Circuit breaker opens, degradation path | `POST /admin/fail-rate/1` on the stub, then submit runs |
| 9 | Locust results vs target, failure concurrency, effect of `--scale` | `make loadtest` |
| 10 | The food-delivery case | Scenario S01 |

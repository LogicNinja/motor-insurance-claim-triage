# Stage 10 service level objective

**Declared:** 2026-11-16. This definition is committed before the first Stage 10 Locust run; use `git log -- docs/slo.md` to verify ordering.

## Objective

For valid FNOLs accepted by `POST /claims`, at least 95% of completed runs must reach a terminal status within **45 seconds at 8 concurrent users**. Terminal statuses include `analyzed`, `escalated`, `degraded`, and `failed`. A valid-request acceptance ratio of at least 95% is also required; HTTP 429 and 5xx submissions count against that ratio. This measures the full API → queue → worker → verifier path, not just the initial 202 response.

## Measurement protocol

- **Workload:** repeated valid food-delivery FNOLs through the local stub LLM (`STUB_FAIL_RATE=0`, `STUB_DELAY_SECONDS=2`); Locust users share one synthetic client ID to measure the per-client quota.
- **Reference point:** 8 concurrent users, 3-minute steady-state run after startup, spawn rate 2 users/second. Each user submits a new claim after its prior run reaches a terminal state, then waits 0.5–2 seconds.
- **Latency sample:** wall-clock time from the start of `POST /claims` through the first observed terminal status from `GET /runs/{id}`. Report p50, p95, and completed-run count. Submission attempts, 429s, 5xxs, and run timeouts are reported separately.
- **Boundary sweep:** 1, 2, 4, 8, 16, and 32 users. The first point that violates either the 45-second p95 or 95% acceptance ratio is the observed failure concurrency. Fewer than 30 completed runs at a point is marked under-sampled.
- **Worker-scale check:** repeat the reference and boundary points with `docker compose up -d --scale worker=3 worker`; report the same metrics and queue behavior.

The 45-second end-to-end budget leaves room for a short local-model call and bounded retrieval, while giving an intake analyst a provisional triage result within one minute. The 8-user objective is an explicit prototype load target, not a production capacity claim.

## Results

Fill this table from Locust CSV output only after the SLO declaration commit. Report accepted runs and rejected submissions so rate limiting cannot make a latency result look better by silently excluding load.

| Protection / workers | Users | Accepted runs | 429 / 5xx | RPS | p50 (s) | p95 (s) | SLO met |
|---|---:|---:|---:|---:|---:|---:|---|

Baseline failure concurrency: pending. Bottleneck: pending.  
`--scale worker=3` effect: pending. Cache effect: pending. Rate-limit effect: pending.

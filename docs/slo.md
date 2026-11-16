# Stage 10 service level objective

**Declared:** 2026-11-16. This definition is committed before the first Stage 10 Locust run; use `git log -- docs/slo.md` to verify ordering.

## Objective

For valid FNOLs accepted by `POST /claims`, at least 95% of completed runs must reach a terminal status within **45 seconds at 4 concurrent users**. Terminal statuses include `analyzed`, `escalated`, `degraded`, and `failed`. At least 95% of valid submission attempts must also receive HTTP 202; HTTP 429 and 5xx submissions count against that ratio. This measures the full API → queue → worker → verifier path, not just the initial 202 response.

## Measurement protocol

- **Workload:** repeated valid food-delivery FNOLs through the local stub LLM (`STUB_FAIL_RATE=0`, `STUB_DELAY_SECONDS=2`); Locust users share one synthetic client ID to measure the per-client quota.
- **Reference point:** 4 concurrent users, 3-minute steady-state run after startup, spawn rate 2 users/second. Each user submits a new claim after its prior run reaches a terminal state, then waits 0.5–2 seconds.
- **Latency sample:** wall-clock time from the start of `POST /claims` through the first observed terminal status from `GET /runs/{id}`. Report p50, p95, and completed-run count. Submission attempts, 429s, 5xxs, and run timeouts are reported separately.
- **Boundary sweep:** 1, 2, 4, 8, 16, and 32 users. The first point that violates either the 45-second p95 or 95% acceptance ratio is the observed failure concurrency. Fewer than 30 completed runs at a point is marked under-sampled.
- **Worker-scale check:** repeat the reference and boundary points with `docker compose up -d --scale worker=3 worker`; report the same metrics and queue behavior.

The 45-second end-to-end budget leaves room for a short local-model call and bounded retrieval, while giving an intake analyst a provisional triage result within one minute. Four concurrent users is an explicit prototype target used to verify that the configured tenant quota supports representative use; it is not a production capacity claim.

## Results

Fill this table from Locust CSV output only after the SLO declaration commit. Report accepted runs and rejected submissions so rate limiting cannot make a latency result look better by silently excluding load.

| Protection / workers | Users | Terminal runs | 202 acceptance | 429 / 5xx | Completed RPS | p50 (s) | p95 (s) | SLO met |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Baseline, fixed 30/min, 1 worker | 1 | 45 | 100% (45/45) | 0 / 0 | 0.25 | 2.7 | 3.2 | Yes |
| Baseline, fixed 30/min, 1 worker | 2 | 90 | 96.8% (91/94) | 3 / 0 | 0.50 | 2.7 | 3.2 | Yes |
| Baseline, fixed 30/min, 1 worker | 4 | 120 | 37.6% (120/319) | 199 / 0 | 0.67 | 2.7 | 3.2 | No |
| Baseline, fixed 30/min, 3 workers | 4 | 109 | 33.6% (110/327) | 217 / 0 | 0.61 | 2.7 | 3.2 | No |
| Cache off, sliding 120/min, 1 worker | 4 | 175 | 100% (176/176) | 0 / 0 | 0.96 | 2.7 | 3.2 | Yes |
| Cache on, sliding 120/min, 1 worker | 4 | 173 | 100% (175/175) | 0 / 0 | 0.96 | 2.7 | 2.7 | Yes |
| Cache on, sliding 120/min, 1 worker | 8 | 357 | 87.4% (360/412) | 52 / 0 | 1.98 | 2.7 | 2.7 | No |

**Baseline failure concurrency:** 4 users (the two-user point passed; four users failed the acceptance-ratio objective). **Baseline bottleneck:** the fixed-window per-client limiter, which returned 429s while accepted-run p95 remained 3.2 seconds. Scaling to three workers did not improve accepted throughput or latency because the limiter was binding. After increasing the quota to 120/min and switching to an atomic sliding window, four users met the SLO; the next measured point, eight users, failed the acceptance ratio with 52 intentional 429 responses and no 5xxs.

**Cache effect at four users with the same sliding-window quota:** cache off p95 was 3.2 seconds; cache on p95 was 2.7 seconds (0.5 seconds / 15.6% lower in this run). The cache-on run recorded 676 hits and 18 misses (97.4% hit rate) across search, clause, and reference reads. Throughput was effectively unchanged (0.96 completed runs/s in both runs), so the latency difference is modest and should be treated as a single-run observation.

The retrieval cache is a bounded process-local LRU with a 60-second TTL and 2,048-entry cap. Cache keys include the corpus build signature, so a reindexed corpus uses fresh entries. Prometheus exports hit/miss counts and live entry count. The API sliding-window limiter is Redis-backed, atomic across API replicas, and returns 503 if Redis cannot enforce the configured quota.

**Rate-limit effect:** with the old fixed 30/minute limit, four users had a 37.6% acceptance ratio. With the sliding 120/minute limit, four users had 100% acceptance; at eight users the rolling quota rejected 52 of 412 attempts (87.4% accepted) without producing 5xxs. The 120/minute value was chosen from the measured four-user offered load (about 106 submissions/minute), with headroom while still bounding a single client's burst rate.

Each point ran for three minutes, except the first exploratory one-user run, which was stopped early and excluded; the completed one-user rerun is shown. `Terminal runs` is the `accepted_run` Locust series. Acceptance ratio is 202 responses divided by valid submission attempts; its numerator equals attempts minus POST failures. Percentiles use only accepted runs that reached a terminal status. Results are single runs; p95 differences below one second are indicative rather than statistically conclusive. Raw Locust CSVs are under ignored `loadtest/results/`.

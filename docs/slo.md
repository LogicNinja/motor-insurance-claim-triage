# Service level objective

Declared on: TODO (date). Commit this file before the first load test; git history is your proof.

- **Measured as:** time from POST /claims to a terminal run status (not the submission call alone).
- **Target:** 95% of complete runs finish within TODO seconds.
- **Justification:** TODO (link to the brief's claims-handling context: high volume, latency-sensitive).

## Results (fill in after measuring)

| Concurrency | RPS | p50 | p95 | Target met |
|-------------|-----|-----|-----|-----------|

Failure concurrency: TODO. Bottleneck: TODO. Effect of `--scale worker=3`: TODO.

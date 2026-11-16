# Motor Insurance Claim Intake and Triage Service

A local multi-service prototype that accepts a First Notice of Loss (FNOL), validates it, retrieves
motor policy wording, checks citations, and routes a recommendation for review. It recommends only;
a human claims handler makes every coverage decision.

Course: UE24AM342AA5 - ML System Design and AgentOps (Mini Project 01).

## Run from a clean checkout

### Requirements

- Git
- Docker Desktop with the Docker Compose v2 plugin, running with Linux containers
- Ports 8001 and 8080 available (API and web client)
- About 5 GB of free disk space for the first image build and local embedding model

No Python installation, LLM API key, or model download is required for the default stack. Docker
downloads service images and the retrieval container downloads its embedding model on first use.

### Start

In PowerShell:

```powershell
git clone https://github.com/LogicNinja/motor-insurance-claim-triage.git "Motor Insurance Claim Intake and Triage Service"
Set-Location "Motor Insurance Claim Intake and Triage Service"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8001/health
```

Wait until the default services report `healthy`. A first build can take several minutes. The health
check for the API confirms it is reachable; the Compose status table shows readiness for the
database, queue, retrieval, verifier, worker, stub LLM, and web client. If startup fails, see
[`docs/runbook.md`](docs/runbook.md).

Build the local policy index once for a fresh checkout. This first ingestion may download the
embedding model and take several minutes; later runs are idempotent when the corpus has not changed:

```powershell
docker compose --profile tools run --rm ingestion python -m ingestion.cli build
```

Open the user interface at http://localhost:8080. It submits a claim, polls for the result, displays
the recommendation and citations, and lets an analyst record feedback. API documentation is at
http://localhost:8001/docs.

### Submit and inspect a claim from PowerShell

```powershell
$claim = @{
  policy_number = "POL-DEMO-0001"
  date_of_loss = "2026-11-15"
  vehicle_registration = "KA01AB1234"
  description = "My parked car was hit by another vehicle and the front bumper was damaged."
} | ConvertTo-Json
$accepted = Invoke-RestMethod http://localhost:8001/claims -Method Post `
  -ContentType "application/json" -Headers @{ "x-client-id" = "readme-demo" } -Body $claim
$runId = $accepted.run_id
$accepted
```

Poll `http://localhost:8001/runs/$runId` until the status is `analyzed`, `escalated`, or another
terminal status. Inspect the persisted execution record at
`http://localhost:8001/runs/$runId/trace`. Recommendations may route to human review, especially
when evidence is incomplete or the language model is unavailable.

### Verify intake smoke checks

The Bash script requires Git Bash or WSL and the Docker CLI in that environment:

```bash
./scripts/smoke.sh
```

It submits one valid food-delivery claim through the real queue, waits for its result, and checks
that malformed input is rejected with field-level validation errors. Rehearsal instructions for the
full demo are in [`docs/demo_checklist.md`](docs/demo_checklist.md).

## Optional services and workflows

- **Observability:** set `OTEL_ENABLED=true` in `.env`, then run
  `docker compose --profile obs up --build -d`. Jaeger: http://localhost:16686, Prometheus:
  http://localhost:9090, Grafana: http://localhost:3000 (default local login `admin` / `admin`).
- **Corpus rebuild:** `docker compose --profile tools run --rm ingestion python -m ingestion.cli build`
- **Evaluation:** install `requirements-dev.txt`, start the stack, then run
  `python eval/run_eval.py --api http://localhost:8001 --output eval/results_local.json`.
- **Load test:** install `requirements-dev.txt`, start the stack, then run
  `locust -f loadtest/locustfile.py --host http://localhost:8001` or `make loadtest`.
- **Scale workers:** `docker compose up -d --scale worker=3 worker`.

Evaluation metric definitions and known limits are documented in [`eval/README.md`](eval/README.md);
the declared load objective and measured results are in [`docs/slo.md`](docs/slo.md).

## Services

| Service | Role |
|---|---|
| `api` | Validation, quarantine, queue submission, status, and feedback endpoints |
| `worker` | Analyst workflow, LLM interface, and routing |
| `verifier` | Separate citation and evidence checks |
| `retrieval` / `chroma` | Local embeddings, clause search, and policy index |
| `redis` | Work queue, retrieval cache, and rate-limit counters |
| `postgres` | Runs, traces, quarantine records, and feedback |
| `stub-llm` | Local, deterministic OpenAI-compatible provider for default runs |
| `web` | Browser-based claim intake and result view |

The runtime configuration is in `.env.example`. Keep real credentials in `.env`; never commit that
file. The project records operational incidents in [`docs/incident_log.md`](docs/incident_log.md).

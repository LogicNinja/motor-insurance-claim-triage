# Runbook

Run commands from the repository root in PowerShell. The default stack uses the local stub LLM;
these procedures do not require a provider key. Do not paste secrets into issue reports or logs.

## Failure 1: LLM provider unavailable or circuit breaker open

**Symptoms:** a run finishes `degraded`/`escalated`, its coverage position is `escalate`, and its
trace has `llm_fallback` or `circuit_breaker_open`.

**Inspect:**

```powershell
docker compose ps
docker compose logs --tail=100 worker stub-llm
Invoke-RestMethod http://localhost:8001/runs/<RUN_ID>/trace
```

For the default stub, inspect its health and failure rate:

```powershell
docker compose exec stub-llm python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```

For an intentional outage rehearsal, set the stub to fail all calls, submit claims, and inspect each
run trace. Restore it immediately after the demonstration:

```powershell
docker compose exec stub-llm python -c "import urllib.request; r=urllib.request.Request('http://localhost:8000/admin/fail-rate/1', method='POST'); print(urllib.request.urlopen(r).read().decode())"
# Submit enough claims to cross BREAKER_FAILURE_THRESHOLD (default 5), then inspect a run.
docker compose exec stub-llm python -c "import urllib.request; r=urllib.request.Request('http://localhost:8000/admin/fail-rate/0', method='POST'); print(urllib.request.urlopen(r).read().decode())"
docker compose restart worker
```

With a hosted provider, check its availability, base URL, model, and credentials in your private
`.env`; then recreate the worker with `docker compose up -d --force-recreate worker`. The fallback
does not make a coverage determination. Existing failed runs are retained for audit; submit a new
claim after service recovery.

## Failure 2: claims remain queued / queue backlog

**Symptoms:** `GET /runs/<RUN_ID>` continues to report `queued` and `updated_at` does not advance.

**Inspect:**

```powershell
docker compose ps
docker compose logs --tail=150 api worker redis
docker compose exec redis redis-cli LLEN analyst
docker compose exec worker celery -A triage_worker.celery_app inspect ping
```

Check that Postgres and Redis are healthy, then restart a failed worker:

```powershell
docker compose restart worker
docker compose ps worker
docker compose logs --tail=100 worker
```

If the queue is still growing and the host has capacity, scale workers:

```powershell
docker compose up -d --scale worker=3 worker
docker compose ps worker
```

Poll the original run again. If the worker cannot recover, preserve its run ID and logs, fix the
underlying error, and submit a new claim. Do not delete Redis or Postgres volumes during recovery;
they contain queued work and the audit trail. A run left queued after a publish failure may require
operator review; the API does not silently claim that it completed.

## Safe shutdown

```powershell
docker compose down
```

This keeps named data volumes. To remove them, use `docker compose down -v` only when you intend to
delete the local database and corpus index.

# Observability

The API, worker, retrieval service, and verifier emit JSON logs and OpenTelemetry spans. Every
request and worker step carries the correlation ID; the API passes W3C `traceparent` context through
Celery and on to retrieval and verification. Span export is opt-in so the normal stack can run
without the observability profile.

## Start the stack with telemetry

In PowerShell, from the repository root, edit `.env` and set these values:

```ini
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318/v1/traces
```

Then recreate the instrumented services with the observability profile enabled:

```powershell
docker compose --profile obs up --build -d --force-recreate
```

Keep `OTEL_ENABLED=true` in `.env`; a temporary `$env:OTEL_ENABLED` setting only applies to that
PowerShell session and can leave later container recreations with tracing disabled. Open Jaeger at
`http://localhost:16686`, Prometheus at `http://localhost:9090`, and Grafana at
`http://localhost:3000` (local default login: `admin` / `admin`). Grafana provisions the
**Motor Claim Triage** dashboard automatically.

## Inspect a run

```powershell
curl.exe http://localhost:8001/runs/<RUN_ID>/trace
```

The `trace` array is persisted in Postgres. Each entry contains `run_id`, `correlation_id`, and
`recorded_at`; tool-call entries include their query or lookup input, returned policy passages,
and latency. Analyst-step and verifier entries include timing, while the LLM step includes prompt
version/hash, model/provider, token usage, latency, and cost. The API response also returns the
correlation ID so it can be searched in JSON logs and used to locate the distributed trace in Jaeger.

Prometheus scrapes the API, worker, retrieval service, and verifier. Worker metrics use Prometheus
multiprocess mode for the Celery prefork pool. The dashboard shows API and worker latency, run
outcomes, estimated LLM spend, retrieval latency, verifier outcomes, and corpus size.

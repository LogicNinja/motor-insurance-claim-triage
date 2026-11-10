# Runbook

## 1. LLM provider unavailable (circuit breaker open)

- **Symptom:** runs finish as `degraded` or `escalated` with a provider-unavailable flag; latency drops to near zero.
- **Confirm:** worker logs show `circuit open`; check `docker compose logs worker`; call the provider health endpoint.
- **Recover:** restore the provider or fix the key in `.env`; wait for the breaker cooldown; re-submit affected runs. TODO: write the exact commands you tested.

## 2. Queue backlog or stalled workers

- **Symptom:** runs stay `queued`; `GET /runs/{id}` never progresses.
- **Confirm:** `docker compose ps`; Redis queue length with `docker compose exec redis redis-cli llen analyst`; worker health.
- **Recover:** restart workers (`docker compose restart worker`) or scale (`docker compose up --scale worker=3`). TODO: confirm the commands and note recovery time.

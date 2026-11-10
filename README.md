# Motor Insurance Claim Intake and Triage Service

A multi-agent service that accepts a First Notice of Loss (FNOL), validates it, retrieves the
relevant policy clauses (including restrictions that limit them), verifies its own answer and
routes the claim with a stated reason. It **recommends**; a human claims handler decides.
It never approves or rejects a claim.

Course: UE24AM342AA5 - ML System Design and AgentOps (Mini Project 01).

## Quickstart

```bash
cp .env.example .env         # then fill in the blanks (secrets, model names)
docker compose up --build    # or: make up
./scripts/smoke.sh           # one valid and one malformed FNOL
```

- API docs: http://localhost:8000/docs
- Web client: http://localhost:8080
- Observability stack (added in a later stage): `docker compose --profile obs up`
- Scale workers: `docker compose up --scale worker=3`

## Services

| Service   | Role |
|-----------|------|
| api       | FastAPI ingestion boundary: validate, quarantine, enqueue, status, feedback |
| worker    | Analyst agent (LangGraph), routing rules, LLM client with retries and breaker |
| verifier  | Second agent: checks every citation resolves to retrieved text |
| retrieval | Embeddings plus Chroma search, clause lookup, cross-reference lookup |
| chroma    | Vector index |
| redis     | Queue, cache, rate-limit counters |
| postgres  | Runs, traces, quarantine, feedback |
| stub-llm  | Fixed responses after a delay, for load tests and demos |
| web       | Minimal client |

## Status

Walking skeleton: intake, validation, quarantine, queue, worker and result storage work end to
end. The analyst agent is a labelled placeholder that always escalates until Stage 4 of the plan.
Replace this section as stages complete.

## Docs

`docs/architecture.md`, `docs/data_contract.md`, `docs/decisions.md`, `docs/runbook.md`,
`docs/incident_log.md`, `docs/slo.md`, `docs/demo_checklist.md`.

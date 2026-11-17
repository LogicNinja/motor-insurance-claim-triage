# Design decisions

| Decision | Chosen | Rejected | Why |
|----------|--------|----------|-----|
| Agent framework | LangGraph | CrewAI | Explicit loop control and bounds |
| Vector index | Chroma | FAISS | Metadata filters and upserts support idempotent ingestion |
| Queue | Redis + Celery | in-process threads | Survives restarts; scales with `--scale` |
| Retrieval placement | Separate retrieval service | Embedding inside worker | Embedding model memory would multiply with scaled workers |
| Verifier placement | Separate container | Second prompt in the worker | Two prompts in one process are not two agents |
| Chunking | One chunk per clause with metadata | Fixed-size windows | A short clause (Chapter 4) must not be buried |
| Routing | Configurable rules | Retrieved from policy text | The wording defines no thresholds |
| Config vs D4 | `.env.example` with placeholders, `make up` copies it | Committed `.env` | No credentials in the repo |

## Operational choices and evaluation notes

The configurable fast-track threshold is INR 25,000. It is an operational prototype rule for routing low-value own-damage claims; it is not a coverage term from the policy. Claims with investigation flags or unsupported evidence are escalated under the routing rules.

Prompt v1 and v2 are evaluated on the same 24-scenario suite by running `eval/run_eval.py` once per version and comparing the outputs with `eval/compare_prompts.py`. That comparison checks suite version, scenario order, provider, model, and prompt version before reporting all four metric differences.

Timeline questions retrieve the policy claim-procedure source and the Citizen Charter separately, filtered by their corpus document IDs. When both are present, the result cites each, flags the differing trigger points, and routes to human review rather than treating the time periods as interchangeable.

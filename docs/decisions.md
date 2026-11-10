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

Add: fast_track_max_inr value and why, prompt v1 vs v2 outcome, what did not work.

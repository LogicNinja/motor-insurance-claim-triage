"""Tools the analyst agent chooses to call. Retrieval lives in its own service (see docs/decisions.md)."""
import httpx

from shared import db
from shared.config import get_settings


def _post(path: str, body: dict) -> dict:
    s = get_settings()
    r = httpx.post(f"{s.retrieval_url}{path}", json=body, timeout=s.llm_timeout_seconds)
    r.raise_for_status()
    return r.json()


def search_corpus(query: str, top_k: int = 5, filters: dict | None = None) -> list:
    """Semantic search. Returns passages with clause_id, chapter, page, text, score."""
    return _post("/search", {"query": query, "top_k": top_k, "filters": filters or {}})["passages"]


def get_clause(clause_id: str) -> dict:
    """Exact lookup of a clause by identifier, e.g. 'ch4' or 'ch2.1.III.a'."""
    s = get_settings()
    r = httpx.get(f"{s.retrieval_url}/clause/{clause_id}", timeout=s.llm_timeout_seconds)
    r.raise_for_status()
    return r.json()


def find_references(term: str) -> list:
    """Resolve a named reference (e.g. 'Limitations as to Use') to the clause that defines it."""
    return _post("/references", {"term": term})["clauses"]


def get_fnol_fields(run_id: str) -> dict:
    """The request's own fields, so the agent can reason about what the claimant actually said."""
    run = db.get_run(run_id)
    return run["request"] if run else {}

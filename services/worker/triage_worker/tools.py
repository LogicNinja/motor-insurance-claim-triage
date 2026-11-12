"""Tools the analyst agent chooses to call. Retrieval lives in its own service (see docs/decisions.md)."""
import httpx
from urllib.parse import quote

from shared import db
from shared.config import get_settings
from shared.logging_setup import correlation_id
from shared.telemetry import inject_trace_context


def _post(path: str, body: dict) -> dict:
    s = get_settings()
    headers = {"x-correlation-id": correlation_id.get(), **inject_trace_context()}
    r = httpx.post(f"{s.retrieval_url}{path}", json=body, headers=headers, timeout=s.llm_timeout_seconds)
    r.raise_for_status()
    return r.json()


def search_corpus(query: str, top_k: int = 5, filters: dict | None = None) -> list:
    """Semantic search. Returns passages with clause_id, chapter, page, text, score."""
    response = _post("/search", {"query": query, "top_k": top_k, "filters": filters or {}})
    return [_normalize(item) for item in response.get("results", response.get("passages", []))]


def _normalize(item: dict) -> dict:
    provenance = item.get("provenance", {})
    return {**provenance, **item, "clause_id": provenance.get("clause_id", item.get("clause_id")),
            "page": provenance.get("page", item.get("page")), "text": item.get("text", "")}


def get_clause(clause_id: str) -> dict:
    """Exact lookup of a clause by identifier, e.g. 'ch4' or 'ch2.1.III.a'."""
    s = get_settings()
    headers = {"x-correlation-id": correlation_id.get(), **inject_trace_context()}
    r = httpx.get(f"{s.retrieval_url}/clause/{quote(clause_id, safe='')}", headers=headers,
                  timeout=s.llm_timeout_seconds)
    r.raise_for_status()
    return r.json()


def find_references(term: str) -> list:
    """Resolve a named reference (e.g. 'Limitations as to Use') to the clause that defines it."""
    return [_normalize(item) for item in _post("/references", {"term": term}).get("clauses", [])]


def get_fnol_fields(run_id: str) -> dict:
    """The request's own fields, so the agent can reason about what the claimant actually said."""
    run = db.get_run(run_id)
    return run["request"] if run else {}

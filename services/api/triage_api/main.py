"""FastAPI entry point: the ingestion boundary (F1, F2, D1) and the asynchronous run API (A4)."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from shared import db
from shared.config import get_settings
from shared.logging_setup import correlation_id, setup_logging
from shared.telemetry import extract_trace_context, inject_trace_context, setup_tracing

from .queue import enqueue_run
from .ratelimit import allow
from .validation import validate_fnol

setup_logging("api")
log = logging.getLogger("api")
tracer = setup_tracing("claim-triage-api")

REQUESTS = Counter("api_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("api_request_seconds", "HTTP request latency in seconds", ["path"])
QUARANTINED = Counter("fnol_quarantined_total", "FNOLs rejected at the ingestion boundary")
ACCEPTED = Counter("fnol_accepted_total", "FNOLs accepted and queued")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Claim Intake and Triage API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in get_settings().cors_origins.split(",") if o],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _retrieval_request(method: str, path: str, body: dict | None = None) -> dict:
    """Expose the internal retrieval endpoints through the public API origin."""
    url = f"{get_settings().retrieval_url.rstrip('/')}{path}"
    try:
        headers = {"x-correlation-id": correlation_id.get(), **inject_trace_context()}
        response = httpx.request(method, url, json=body, headers=headers, timeout=get_settings().llm_timeout_seconds)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "Retrieval request failed")
        except (ValueError, AttributeError):
            detail = "Retrieval request failed"
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Retrieval service unavailable") from exc


@app.middleware("http")
async def correlation_and_metrics(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex
    token = correlation_id.set(cid)
    start = time.perf_counter()
    status = 500
    path = "/runs/{id}" if request.url.path.startswith("/runs/") else request.url.path
    parent = extract_trace_context(dict(request.headers))
    try:
        with tracer.start_as_current_span(
            f"HTTP {request.method} {path}", context=parent,
            attributes={"http.request.method": request.method, "url.path": path, "correlation_id": cid},
        ) as span:
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["x-correlation-id"] = cid
                span.set_attribute("http.response.status_code", status)
                if status >= 500:
                    from opentelemetry.trace import Status, StatusCode
                    span.set_status(Status(StatusCode.ERROR))
                return response
            except Exception as exc:
                span.record_exception(exc)
                from opentelemetry.trace import Status, StatusCode
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                elapsed = time.perf_counter() - start
                REQUESTS.labels(request.method, path, str(status)).inc()
                LATENCY.labels(path).observe(elapsed)
                log.info(
                    "request",
                    extra={"fields": {"method": request.method, "path": request.url.path,
                                      "status": status, "duration_ms": round(elapsed * 1000, 1)}},
                )
    finally:
        correlation_id.reset(token)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/clause/{clause_id}")
def get_clause(clause_id: str):
    return _retrieval_request("GET", f"/clause/{quote(clause_id, safe='')}")


class ReferenceRequest(BaseModel):
    term: str


@app.post("/references")
def find_references(body: ReferenceRequest):
    return _retrieval_request("POST", "/references", body.model_dump())


@app.post("/claims", status_code=202)
async def submit_claim(request: Request, x_client_id: Optional[str] = Header(default=None)):
    """Accept an FNOL. Invalid input is quarantined and NEVER starts an agent run."""
    client = x_client_id or (request.client.host if request.client else "unknown")
    if not allow(client):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    try:
        payload = await request.json()
    except Exception:
        payload = None
    errors = validate_fnol(payload)
    cid = correlation_id.get()
    if errors:
        qid = db.add_quarantine(payload, errors, cid)
        QUARANTINED.inc()
        return JSONResponse(status_code=422, content={"status": "rejected", "quarantine_id": qid, "errors": errors})
    run_id = uuid.uuid4().hex
    db.create_run(run_id, payload, cid)
    enqueue_run(run_id, cid)
    ACCEPTED.inc()
    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run["run_id"], "status": run["status"], "result": run["result"],
            "created_at": run["created_at"], "updated_at": run["updated_at"]}


@app.get("/runs/{run_id}/trace")
def get_trace(run_id: str):
    """Everything needed to reconstruct a run (D5): steps, queries, passages, latency, cost."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run_id, "correlation_id": run["correlation_id"], "trace": run["trace"]}


class Feedback(BaseModel):
    satisfactory: bool
    reason: Optional[str] = None


@app.post("/runs/{run_id}/feedback", status_code=201)
def post_feedback(run_id: str, body: Feedback):
    if not db.get_run(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    db.add_feedback(run_id, body.satisfactory, body.reason)
    return {"status": "recorded"}

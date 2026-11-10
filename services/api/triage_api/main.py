"""FastAPI entry point: the ingestion boundary (F1, F2, D1) and the asynchronous run API (A4)."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from shared import db
from shared.config import get_settings
from shared.logging_setup import correlation_id, setup_logging

from .queue import enqueue_run
from .ratelimit import allow
from .validation import validate_fnol

setup_logging("api")
log = logging.getLogger("api")

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


@app.middleware("http")
async def correlation_and_metrics(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex
    token = correlation_id.set(cid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["x-correlation-id"] = cid
        return response
    finally:
        elapsed = time.perf_counter() - start
        path = "/runs/{id}" if request.url.path.startswith("/runs/") else request.url.path
        REQUESTS.labels(request.method, path, str(status)).inc()
        LATENCY.labels(path).observe(elapsed)
        log.info(
            "request",
            extra={"fields": {"method": request.method, "path": request.url.path,
                              "status": status, "duration_ms": round(elapsed * 1000, 1)}},
        )
        correlation_id.reset(token)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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

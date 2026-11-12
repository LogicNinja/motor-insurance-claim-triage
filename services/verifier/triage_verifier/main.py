import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from shared.logging_setup import setup_logging
from shared.logging_setup import correlation_id
from shared.telemetry import extract_trace_context, setup_tracing

from .verify import verify

setup_logging("verifier")
log = logging.getLogger("verifier")
tracer = setup_tracing("claim-triage-verifier")
app = FastAPI(title="Verifier agent", version="0.1.0")
VERDICTS = Counter("verifier_verdicts_total", "Verification outcomes", ["verified"])


@app.middleware("http")
async def trace_and_log_request(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex
    token = correlation_id.set(cid)
    parent = extract_trace_context(dict(request.headers))
    start = time.perf_counter()
    status = 500
    try:
        with tracer.start_as_current_span(f"HTTP {request.method} {request.url.path}", context=parent,
                                          attributes={"correlation_id": cid, "http.request.method": request.method}) as span:
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["x-correlation-id"] = cid
                span.set_attribute("http.response.status_code", status)
                return response
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                log.info("request", extra={"fields": {"method": request.method, "path": request.url.path,
                           "status": status, "duration_ms": round((time.perf_counter()-start)*1000, 1)}})
    finally:
        correlation_id.reset(token)


class VerifyRequest(BaseModel):
    draft: dict
    passages: list[dict]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/verify")
def verify_endpoint(req: VerifyRequest):
    result = verify(req.draft, req.passages)
    VERDICTS.labels(str(result["verified"]).lower()).inc()
    from opentelemetry import trace
    span = trace.get_current_span()
    span.set_attribute("verification.verified", bool(result["verified"]))
    span.set_attribute("verification.citation_count", len(req.draft.get("citations", [])))
    span.set_attribute("verification.passage_count", len(req.passages))
    return result

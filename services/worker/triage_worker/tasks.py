"""Celery task that executes one claim run."""
import logging
import time

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from shared import db
from shared.logging_setup import correlation_id, setup_logging
from shared.telemetry import extract_trace_context

from .celery_app import app
from .graph import analyse
from .metrics import LLM_CALLS, LLM_COST_USD, RUNS, RUN_DURATION

setup_logging("worker")
log = logging.getLogger("worker")
tracer = trace.get_tracer("triage-worker")


@app.task(name="triage.process_claim", bind=True)
def process_claim(self, run_id: str, cid: str = "-") -> None:
    token = correlation_id.set(cid)
    started = time.perf_counter()
    final_status = "failed"
    parent = extract_trace_context(getattr(self.request, "headers", None) or {})
    try:
        with tracer.start_as_current_span("triage.process_claim", context=parent,
                                          attributes={"run_id": run_id, "correlation_id": cid}) as span:
            run = db.get_run(run_id)
            if run is None:
                log.error("run not found", extra={"fields": {"run_id": run_id}})
                span.set_status(Status(StatusCode.ERROR, "run not found"))
                return
            db.set_status(run_id, "analysing")
            try:
                result = analyse(run_id, run["request"])
                for trace_step in result.get("agent_trace", []):
                    db.append_trace(run_id, trace_step)
                    if trace_step.get("event") == "llm_draft":
                        provider = trace_step.get("provider", "unknown")
                        LLM_CALLS.labels(provider, "success").inc()
                        cost = trace_step.get("cost_usd")
                        if cost is not None:
                            LLM_COST_USD.labels(provider).inc(float(cost))
                    elif trace_step.get("event") == "llm_fallback" and trace_step.get("provider_call_failed"):
                        LLM_CALLS.labels(trace_step.get("provider", "unknown"), "failed").inc()
                db.save_result(run_id, result["status"], result)
                final_status = result["status"]
            except Exception as exc:
                log.exception("run failed", extra={"fields": {"run_id": run_id}})
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                try:
                    db.append_trace(run_id, {"event": "worker_failure", "reason": type(exc).__name__})
                except Exception:
                    log.exception("failed to persist worker failure trace", extra={"fields": {"run_id": run_id}})
                db.save_result(run_id, "failed", {
                    "run_id": run_id,
                    "status": "failed",
                    "coverage": {"position": "escalate", "citations": [], "verified": False,
                                 "summary": "The run did not complete; no position was produced."},
                    "routing": {"route": "human_review", "escalated": True,
                                "reason": "Run failed before completion.", "flags": ["run_failed"]},
                })
            else:
                log.info("run finished", extra={"fields": {"run_id": run_id, "status": result["status"]}})
    finally:
        RUNS.labels(final_status).inc()
        RUN_DURATION.observe(time.perf_counter() - started)
        correlation_id.reset(token)

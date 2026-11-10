"""Celery task that executes one claim run."""
import logging

from shared import db
from shared.logging_setup import correlation_id, setup_logging

from .celery_app import app
from .graph import analyse

setup_logging("worker")
log = logging.getLogger("worker")


@app.task(name="triage.process_claim")
def process_claim(run_id: str, cid: str = "-") -> None:
    token = correlation_id.set(cid)
    try:
        run = db.get_run(run_id)
        if run is None:
            log.error("run not found", extra={"fields": {"run_id": run_id}})
            return
        db.set_status(run_id, "analysing")
        try:
            result = analyse(run_id, run["request"])
            db.save_result(run_id, result["status"], result)
        except Exception:
            log.exception("run failed", extra={"fields": {"run_id": run_id}})
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
        correlation_id.reset(token)

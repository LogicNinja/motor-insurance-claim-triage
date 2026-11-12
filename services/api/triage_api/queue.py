"""Queue client. Sends tasks by name so the api image stays independent of the worker image."""
from celery import Celery

from shared.config import get_settings
from shared.telemetry import inject_trace_context

_app = None


def _celery() -> Celery:
    global _app
    if _app is None:
        url = get_settings().redis_url
        _app = Celery("triage_api", broker=url, backend=url)
    return _app


def enqueue_run(run_id: str, correlation_id: str) -> None:
    headers = inject_trace_context({"correlation_id": correlation_id})
    _celery().send_task("triage.process_claim", args=[run_id, correlation_id], queue="analyst", headers=headers)

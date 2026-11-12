from celery import Celery
from celery.signals import worker_process_init, worker_ready
import os
from prometheus_client import CollectorRegistry, multiprocess, start_http_server
from shared.telemetry import setup_tracing

from shared.config import get_settings

_url = get_settings().redis_url

app = Celery("triage_worker", broker=_url, backend=_url, include=["triage_worker.tasks"])
app.conf.task_default_queue = "analyst"
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1

_metrics_httpd = None


@worker_ready.connect
def _start_worker_metrics(**_kwargs):
    global _metrics_httpd
    if _metrics_httpd is None:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        _metrics_httpd, _ = start_http_server(int(os.getenv("WORKER_METRICS_PORT", "9100")), registry=registry)


@worker_process_init.connect
def _configure_worker_tracing(**_kwargs):
    setup_tracing("claim-triage-worker")

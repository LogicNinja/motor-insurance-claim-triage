from celery import Celery

from shared.config import get_settings

_url = get_settings().redis_url

app = Celery("triage_worker", broker=_url, backend=_url, include=["triage_worker.tasks"])
app.conf.task_default_queue = "analyst"
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1

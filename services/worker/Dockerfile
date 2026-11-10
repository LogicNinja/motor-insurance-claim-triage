FROM python:3.12-slim
WORKDIR /srv
COPY services/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/worker/triage_worker ./triage_worker
CMD ["celery", "-A", "triage_worker.celery_app", "worker", "-Q", "analyst", "--loglevel=info"]

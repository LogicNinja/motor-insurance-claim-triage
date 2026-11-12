FROM python:3.12-slim
WORKDIR /srv
COPY services/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /tmp/prometheus
COPY shared ./shared
COPY services/worker/triage_worker ./triage_worker
CMD ["sh", "-c", "rm -f /tmp/prometheus/*.db && exec celery -A triage_worker.celery_app worker -Q analyst --loglevel=info"]

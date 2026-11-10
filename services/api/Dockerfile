FROM python:3.12-slim
WORKDIR /srv
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/api/triage_api ./triage_api
CMD ["uvicorn", "triage_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

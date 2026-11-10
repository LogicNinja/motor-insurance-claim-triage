FROM python:3.12-slim
WORKDIR /srv
COPY services/verifier/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/verifier/triage_verifier ./triage_verifier
CMD ["uvicorn", "triage_verifier.main:app", "--host", "0.0.0.0", "--port", "8000"]

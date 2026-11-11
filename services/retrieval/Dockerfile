FROM python:3.12-slim
WORKDIR /srv
COPY services/retrieval/requirements.txt .
# CPU-only local embeddings avoid downloading an unused CUDA runtime.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/retrieval/triage_retrieval ./triage_retrieval
CMD ["uvicorn", "triage_retrieval.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim
WORKDIR /srv
COPY ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ingestion ./ingestion
CMD ["python", "-m", "ingestion.cli", "--help"]

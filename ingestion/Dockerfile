
FROM python:3.12-slim

WORKDIR /srv

ENV PYTHONUNBUFFERED=1
ENV CORPUS_DIR=/corpus
ENV CHROMA_HOST=chroma
ENV CHROMA_PORT=8000

COPY ingestion/requirements.txt .
# The pipeline embeds on CPU. Install the CPU-only PyTorch wheel so pip does
# not pull the much larger CUDA runtime into this image.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0
RUN pip install --no-cache-dir -r requirements.txt

COPY ingestion ./ingestion

CMD ["python", "-m", "ingestion.cli", "--help"]

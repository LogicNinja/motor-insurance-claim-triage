from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from shared.logging_setup import setup_logging

from .verify import verify

setup_logging("verifier")
app = FastAPI(title="Verifier agent", version="0.1.0")
VERDICTS = Counter("verifier_verdicts_total", "Verification outcomes", ["verified"])


class VerifyRequest(BaseModel):
    draft: dict
    passages: list[dict]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/verify")
def verify_endpoint(req: VerifyRequest):
    result = verify(req.draft, req.passages)
    VERDICTS.labels(str(result["verified"]).lower()).inc()
    return result

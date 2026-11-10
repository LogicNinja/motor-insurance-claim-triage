"""Stub LLM: fixed answers after a delay, so tests measure your system, not the provider.

POST /admin/fail-rate/{rate} (0..1) makes the stub fail on demand, e.g. for the circuit-breaker demo.
"""
import asyncio
import os
import random

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Stub LLM")
DELAY = float(os.environ.get("STUB_DELAY_SECONDS", "2.0"))
JITTER = float(os.environ.get("STUB_JITTER_SECONDS", "0.3"))
fail_rate = float(os.environ.get("STUB_FAIL_RATE", "0"))


@app.get("/health")
def health():
    return {"status": "ok", "fail_rate": fail_rate}


@app.post("/admin/fail-rate/{rate}")
def set_fail_rate(rate: float):
    global fail_rate
    fail_rate = max(0.0, min(1.0, rate))
    return {"fail_rate": fail_rate}


@app.post("/v1/chat/completions")
async def chat(payload: dict):
    await asyncio.sleep(max(0.0, DELAY + random.uniform(-JITTER, JITTER)))
    if random.random() < fail_rate:
        raise HTTPException(status_code=503, detail="stub provider unavailable")
    return {
        "id": "stub",
        "object": "chat.completion",
        "model": "stub",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "{\"stub\": true}"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 120, "total_tokens": 520},
    }

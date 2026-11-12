"""Prometheus metrics emitted by the worker's prefork task processes."""
import os
from pathlib import Path

from prometheus_client import Counter, Histogram

_multiprocess_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
if _multiprocess_dir:
    Path(_multiprocess_dir).mkdir(parents=True, exist_ok=True)

RUNS = Counter("triage_worker_runs_total", "Claim runs processed by the worker", ["status"])
RUN_DURATION = Histogram("triage_worker_run_duration_seconds", "End-to-end worker claim-run duration")
TOOL_CALLS = Counter("triage_worker_tool_calls_total", "Analyst tool calls", ["tool", "status"])
LLM_CALLS = Counter("triage_worker_llm_calls_total", "LLM calls from analyst runs", ["provider", "status"])
LLM_COST_USD = Counter("triage_worker_llm_cost_usd_total", "Estimated LLM spend in USD", ["provider"])

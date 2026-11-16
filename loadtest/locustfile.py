"""End-to-end valid claim workload against the stub LLM.

Headless example (run after committing docs/slo.md):
  locust -f loadtest/locustfile.py --host http://localhost:8001 --headless -u 8 -r 2 --run-time 3m --csv loadtest/results/baseline-8

All simulated users share LOADTEST_CLIENT_ID to exercise one partner's API quota.
"""
import os
import time
import uuid

from locust import HttpUser, between, events, task

TERMINAL = {"analyzed", "completed", "escalated", "degraded", "failed"}
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "0.5"))
RUN_TIMEOUT = float(os.environ.get("LOADTEST_RUN_TIMEOUT_SECONDS", "240"))
CLIENT_ID = os.environ.get("LOADTEST_CLIENT_ID", f"locust-{uuid.uuid4().hex}")
FNOL = {
    "policy_number": "POL-TEST-0001",
    "date_of_loss": "2026-10-05",
    "vehicle_registration": "KA01AB1234",
    "description": "I was making a food delivery run in my own car when the accident happened.",
}


class ClaimUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        self.client.headers.update({"x-client-id": CLIENT_ID})

    @staticmethod
    def _record_run(started, name, error=None):
        events.request.fire(
            request_type="RUN",
            name=name,
            response_time=(time.perf_counter() - started) * 1000,
            response_length=0,
            exception=error,
            context={},
        )

    @task
    def complete_run(self):
        started = time.perf_counter()
        r = self.client.post("/claims", json=FNOL, name="POST /claims")
        if r.status_code != 202:
            self._record_run(started, "complete_run", RuntimeError(f"claim submission returned HTTP {r.status_code}"))
            return
        run_id = r.json()["run_id"]
        while time.perf_counter() - started < RUN_TIMEOUT:
            s = self.client.get(f"/runs/{run_id}", name="GET /runs/{id}")
            if s.ok and s.json()["status"] in TERMINAL:
                self._record_run(started, "accepted_run")
                self._record_run(started, "complete_run")
                return
            time.sleep(POLL_SECONDS)
        error = TimeoutError("run did not finish")
        self._record_run(started, "accepted_run", error)
        self._record_run(started, "complete_run", error)

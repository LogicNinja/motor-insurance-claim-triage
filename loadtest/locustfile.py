"""Load test against the stub LLM. Declare the SLO in docs/slo.md BEFORE the first run.

Run: make loadtest, then open http://localhost:8089. The custom 'complete_run' entry is the end-to-end time.
"""
import os
import time

from locust import HttpUser, between, events, task

TERMINAL = {"completed", "escalated", "degraded", "failed"}
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "0.5"))
RUN_TIMEOUT = float(os.environ.get("RUN_TIMEOUT_SECONDS", "120"))
FNOL = {
    "policy_number": "POL-TEST-0001",
    "date_of_loss": "2026-10-05",
    "vehicle_registration": "KA01AB1234",
    "description": "I was making a food delivery run in my own car when the accident happened.",
}


class ClaimUser(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def complete_run(self):
        started = time.perf_counter()
        r = self.client.post("/claims", json=FNOL, name="POST /claims")
        if r.status_code != 202:
            return
        run_id = r.json()["run_id"]
        while time.perf_counter() - started < RUN_TIMEOUT:
            s = self.client.get(f"/runs/{run_id}", name="GET /runs/{id}")
            if s.ok and s.json()["status"] in TERMINAL:
                events.request.fire(request_type="RUN", name="complete_run",
                                    response_time=(time.perf_counter() - started) * 1000,
                                    response_length=0, exception=None, context={})
                return
            time.sleep(POLL_SECONDS)
        events.request.fire(request_type="RUN", name="complete_run",
                            response_time=RUN_TIMEOUT * 1000, response_length=0,
                            exception=TimeoutError("run did not finish"), context={})

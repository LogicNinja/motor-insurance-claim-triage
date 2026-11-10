"""LLM client behind an interface you control, so the provider is changed by configuration only."""
import random
import time

import httpx

from shared.config import get_settings


class CircuitOpenError(RuntimeError):
    """Raised while the breaker is open: fail fast instead of piling up on a dead provider."""


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown: float):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown:
            return True  # half-open: allow one probe
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


class LLMClient:
    def __init__(self):
        s = get_settings()
        self.s = s
        self.breaker = CircuitBreaker(s.breaker_failure_threshold, s.breaker_cooldown_seconds)

    def complete(self, messages: list, prompt_version: str, step: str) -> dict:
        """Returns {text, usage, latency_s}. TODO(L5): compute cost from usage and append a trace step."""
        if not self.breaker.allow():
            raise CircuitOpenError("circuit open: provider marked unavailable")
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}"} if self.s.llm_api_key else {}
        last_exc: Exception | None = None
        for attempt in range(1, self.s.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                r = httpx.post(
                    f"{self.s.llm_base_url}/v1/chat/completions",
                    json={"model": self.s.llm_model, "messages": messages},
                    headers=headers,
                    timeout=self.s.llm_timeout_seconds,
                )
                r.raise_for_status()
                data = r.json()
                self.breaker.record_success()
                return {
                    "text": data["choices"][0]["message"]["content"],
                    "usage": data.get("usage", {}),
                    "latency_s": time.perf_counter() - started,
                }
            except httpx.HTTPError as exc:
                last_exc = exc
                self.breaker.record_failure()
                if attempt == self.s.llm_max_retries or not self.breaker.allow():
                    break
                time.sleep(self.s.llm_backoff_base_seconds * 2 ** (attempt - 1) + random.random() * 0.1)
        raise last_exc if last_exc else RuntimeError("LLM call failed")

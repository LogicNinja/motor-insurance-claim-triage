"""OpenAI-compatible LLM client with retries, circuit breaking and per-call cost metadata."""
import random
import os
import threading
import time
from urllib.parse import urlparse

import httpx

from shared.config import get_settings


class CircuitOpenError(RuntimeError):
    """Raised while the breaker is open: fail fast instead of piling up on a dead provider."""

    def __init__(self, message: str, failures: int = 0):
        super().__init__(message)
        self.failures = failures
        self.breaker_open = True


class ProviderUnavailableError(RuntimeError):
    """A timed-out or failed provider call after the configured retry policy."""

    def __init__(self, message: str, *, attempts: int, status_code: int | None,
                 failures: int, breaker_open: bool, error_type: str):
        super().__init__(message)
        self.attempts = attempts
        self.status_code = status_code
        self.failures = failures
        self.breaker_open = breaker_open
        self.error_type = error_type


class LLMConfigurationError(RuntimeError):
    """Raised when a hosted provider is selected without its model or key."""


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown: float):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: float | None = None
        self.half_open_probe = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return True
            if time.monotonic() - self.opened_at < self.cooldown:
                return False
            if self.half_open_probe:
                return False
            self.half_open_probe = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None
            self.half_open_probe = False

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.half_open_probe = False
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self.opened_at is not None and time.monotonic() - self.opened_at < self.cooldown


def _endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _token_count(usage: dict, *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0
    return 0


class LLMClient:
    def __init__(self):
        self.s = get_settings()
        self.breaker = CircuitBreaker(self.s.breaker_failure_threshold, self.s.breaker_cooldown_seconds)

    def complete(self, messages: list, prompt_version: str, step: str, prompt_hash: str | None = None) -> dict:
        """Return the model response plus auditable prompt, token, latency and cost metadata."""
        if not self.s.llm_base_url:
            raise LLMConfigurationError("LLM_BASE_URL is not configured")
        is_stub = "stub-llm" in urlparse(self.s.llm_base_url).hostname if urlparse(self.s.llm_base_url).hostname else False
        if not is_stub and not self.s.llm_api_key:
            raise LLMConfigurationError("LLM_API_KEY is required for a hosted provider")
        if not is_stub and not self.s.llm_model:
            raise LLMConfigurationError("LLM_MODEL is required for a hosted provider")
        if not self.breaker.allow():
            raise CircuitOpenError("circuit open: provider marked unavailable", self.breaker.failures)

        model = self.s.llm_model or ("stub" if is_stub else "")
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}"} if self.s.llm_api_key else {}
        payload = {"model": model, "messages": messages}
        last_exc: Exception | None = None
        for attempt in range(1, self.s.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(_endpoint(self.s.llm_base_url), json=payload, headers=headers,
                                      timeout=self.s.llm_timeout_seconds)
                response.raise_for_status()
                data = response.json()
                usage = data.get("usage", {}) or {}
                input_tokens = _token_count(usage, "prompt_tokens", "input_tokens")
                output_tokens = _token_count(usage, "completion_tokens", "output_tokens")
                input_rate = self.s.llm_input_usd_per_1m
                output_rate = self.s.llm_output_usd_per_1m
                cost = None
                if input_rate is not None and output_rate is not None:
                    cost = round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 12)
                self.breaker.record_success()
                return {
                    "text": data["choices"][0]["message"]["content"],
                    "provider": self.s.llm_provider or ("stub" if is_stub else urlparse(self.s.llm_base_url).hostname or "configured-provider"),
                    "model": data.get("model") or model,
                    "step": step,
                    "prompt_version": prompt_version,
                    "prompt_id": f"analyst:{prompt_version}",
                    "prompt_hash": prompt_hash,
                    "usage": {**usage, "input_tokens": input_tokens, "output_tokens": output_tokens,
                              "total_tokens": input_tokens + output_tokens},
                    "latency_s": round(time.perf_counter() - started, 6),
                    "cost_usd": cost,
                    "cost_currency": "USD",
                    "cost_status": "estimated" if cost is not None else "rate_not_configured",
                }
            except httpx.HTTPError as exc:
                last_exc = exc
                self.breaker.record_failure()
                if attempt == self.s.llm_max_retries or not self.breaker.allow():
                    break
                time.sleep(self.s.llm_backoff_base_seconds * 2 ** (attempt - 1) + random.random() * 0.1)
        if last_exc:
            response = getattr(last_exc, "response", None)
            raise ProviderUnavailableError(
                "provider requpest failed after configured retries",
                attempts=attempt,
                status_code=getattr(response, "status_code", None),
                failures=self.breaker.failures,
                breaker_open=self.breaker.is_open,
                error_type=type(last_exc).__name__,
            ) from last_exc
        raise RuntimeError("LLM call failed")


_PROCESS_CLIENT: LLMClient | None = None
_PROCESS_CLIENT_PID: int | None = None


def get_llm_client() -> LLMClient:
    """Reuse breaker state per worker process; recreate after Celery forks a child."""
    global _PROCESS_CLIENT, _PROCESS_CLIENT_PID
    pid = os.getpid()
    if _PROCESS_CLIENT is None or _PROCESS_CLIENT_PID != pid:
        _PROCESS_CLIENT = LLMClient()
        _PROCESS_CLIENT_PID = pid
    return _PROCESS_CLIENT

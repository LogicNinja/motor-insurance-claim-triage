import httpx
import pytest

from triage_worker.llm import CircuitOpenError, LLMClient, ProviderUnavailableError, _endpoint
from triage_worker.prompts import load_prompt


def test_openai_compatible_endpoint_resolution():
    assert _endpoint("https://provider.example/v1") == "https://provider.example/v1/chat/completions"
    assert _endpoint("https://provider.example") == "https://provider.example/v1/chat/completions"
    assert _endpoint("http://stub-llm:8000/v1/chat/completions") == "http://stub-llm:8000/v1/chat/completions"


def test_prompt_versions_are_pinned_and_hashed():
    prompt = load_prompt("analyst", "v2")
    assert prompt["id"] == "analyst:v2"
    assert len(prompt["sha256"]) == 64


def test_completion_records_prompt_usage_latency_and_cost(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER", "unit-test-provider")
    monkeypatch.setenv("LLM_INPUT_USD_PER_1M", "2")
    monkeypatch.setenv("LLM_OUTPUT_USD_PER_1M", "5")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"model": "test-model", "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    monkeypatch.setattr("triage_worker.llm.httpx.post", lambda *args, **kwargs: FakeResponse())
    result = LLMClient().complete([], "v2", "draft_position", "a" * 64)
    assert result["prompt_version"] == "v2"
    assert result["provider"] == "unit-test-provider"
    assert result["prompt_hash"] == "a" * 64
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["output_tokens"] == 20
    assert result["cost_usd"] == 0.0003
    assert result["cost_status"] == "estimated"


def test_retries_then_opens_breaker_and_short_circuits(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("BREAKER_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("BREAKER_COOLDOWN_SECONDS", "30")
    calls = []
    sleeps = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr("triage_worker.llm.httpx.post", fail)
    monkeypatch.setattr("triage_worker.llm.time.sleep", lambda delay: sleeps.append(delay))
    client = LLMClient()
    with pytest.raises(ProviderUnavailableError) as caught:
        client.complete([], "v1", "draft_position")
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert caught.value.breaker_open is True

    with pytest.raises(CircuitOpenError):
        client.complete([], "v1", "draft_position")
    assert len(calls) == 3

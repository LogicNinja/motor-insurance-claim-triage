from opentelemetry.sdk.trace import TracerProvider
from opentelemetry import trace

from shared.telemetry import extract_trace_context, inject_trace_context
from triage_worker.graph import _tool


def test_tool_trace_persists_query_passages_and_latency():
    state = {"tool_calls": 0, "trace": []}
    passage = {
        "chunk_id": "policy:ch4:p19", "clause_id": "CHAPTER 4 - Limitations as to Use",
        "page": 19, "text": "Use excludes hire or reward.", "distance": 0.12,
    }
    result = _tool(state, "search_corpus", lambda query, top_k: [passage], "food delivery restrictions", 5)
    event = state["trace"][0]
    assert result == [passage]
    assert event["event"] == "tool_call" and event["ok"] is True
    assert event["input"] == {"query": "food delivery restrictions", "top_k": 5, "filters": {}}
    assert event["passages"][0]["clause_id"] == passage["clause_id"]
    assert event["passages"][0]["page"] == 19
    assert event["latency_s"] >= 0


def test_w3c_trace_context_round_trips_between_services():
    tracer = TracerProvider().get_tracer("observability-test")
    with tracer.start_as_current_span("api-request") as parent:
        headers = inject_trace_context({"correlation_id": "corr-123"})
        child_context = extract_trace_context(headers)
        child_parent = trace.get_current_span(child_context).get_span_context()
        assert headers["correlation_id"] == "corr-123"
        assert child_parent.trace_id == parent.get_span_context().trace_id
        assert child_parent.span_id == parent.get_span_context().span_id

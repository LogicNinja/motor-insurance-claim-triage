import datetime as dt

from triage_api.validation import cross_field_errors, validate_fnol

TODAY = dt.date(2026, 10, 9)
VALID = {
    "policy_number": "POL-TEST-0001",
    "date_of_loss": "2026-10-05",
    "vehicle_registration": "KA01AB1234",
    "description": "I was making a food delivery run in my own car when the accident happened.",
}


def fields(errors):
    return {e["field"] for e in errors}


def test_valid_fnol_passes():
    assert validate_fnol(VALID, today=TODAY) == []


def test_missing_required_field():
    bad = {k: v for k, v in VALID.items() if k != "policy_number"}
    assert "policy_number" in fields(validate_fnol(bad, today=TODAY))


def test_bad_registration_and_short_description():
    bad = dict(VALID, vehicle_registration="not-a-plate", description="short")
    assert {"vehicle_registration", "description"} <= fields(validate_fnol(bad, today=TODAY))


def test_range_violation():
    bad = dict(VALID, estimated_damage_inr=-5)
    assert "estimated_damage_inr" in fields(validate_fnol(bad, today=TODAY))


def test_category_violation():
    bad = dict(VALID, claim_type_hint="alien_invasion")
    assert "claim_type_hint" in fields(validate_fnol(bad, today=TODAY))


def test_unknown_field_rejected():
    assert "surprise" in fields(validate_fnol(dict(VALID, surprise=1), today=TODAY))


def test_non_object_body():
    assert validate_fnol(["not", "an", "object"], today=TODAY)[0]["field"] == "$"


def test_cross_field_future_date():
    errs = cross_field_errors(dict(VALID, date_of_loss="2999-01-01"), TODAY)
    assert fields(errs) == {"date_of_loss"}


def test_cross_field_fir_rules():
    assert fields(cross_field_errors(dict(VALID, fir_lodged=True), TODAY)) == {"fir_number"}
    assert fields(cross_field_errors(dict(VALID, fir_lodged=False, fir_number="12/2026"), TODAY)) == {"fir_number"}
    assert cross_field_errors(dict(VALID, fir_lodged=True, fir_number="12/2026"), TODAY) == []


def test_invalid_claim_is_quarantined_without_creating_or_queueing_a_run(monkeypatch):
    from fastapi.testclient import TestClient

    from triage_api import main as api

    quarantined = []
    created_runs = []
    enqueued_runs = []

    monkeypatch.setattr(api.db, "init_db", lambda: None)
    monkeypatch.setattr(api, "allow", lambda _client: True)
    monkeypatch.setattr(
        api.db,
        "add_quarantine",
        lambda payload, errors, correlation_id: (
            quarantined.append((payload, errors, correlation_id)) or 17
        ),
    )
    monkeypatch.setattr(api.db, "create_run", lambda *args: created_runs.append(args))
    monkeypatch.setattr(api, "enqueue_run", lambda *args: enqueued_runs.append(args))

    invalid = {
        "policy_number": "x",
        "date_of_loss": "2999-01-01",
        "vehicle_registration": "not-a-plate",
        "description": "short",
        "estimated_damage_inr": -5,
    }
    with TestClient(api.app) as client:
        response = client.post("/claims", json=invalid, headers={"x-correlation-id": "test-invalid-fnol"})

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "rejected"
    assert body["quarantine_id"] == 17
    assert {error["field"] for error in body["errors"]} >= {
        "policy_number",
        "date_of_loss",
        "vehicle_registration",
        "description",
        "estimated_damage_inr",
    }
    assert len(quarantined) == 1
    assert quarantined[0][0] == invalid
    assert quarantined[0][2] == "test-invalid-fnol"
    assert created_runs == []
    assert enqueued_runs == []

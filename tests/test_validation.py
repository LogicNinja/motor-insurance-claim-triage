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

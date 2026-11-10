"""Validation at the ingestion boundary (F2, D1). Returns field-level errors; empty list means valid."""
import datetime as dt
import json
import os
from typing import Optional

_VALIDATOR = None


def _load_validator():
    # Imported lazily so the pure cross-field rules can be tested without jsonschema installed.
    from jsonschema import Draft202012Validator, FormatChecker

    from shared.config import get_settings

    path = os.path.join(get_settings().schema_dir, "fnol.schema.json")
    with open(path, encoding="utf-8") as f:
        return Draft202012Validator(json.load(f), format_checker=FormatChecker())


def _field_of(err) -> str:
    if err.validator in ("required", "additionalProperties"):
        parts = err.message.split("'")
        if len(parts) > 1:
            return parts[1]
    return ".".join(str(p) for p in err.path) or "$"


def cross_field_errors(payload: dict, today: Optional[dt.date] = None) -> list:
    """Rules a schema cannot express. Keep these in sync with docs/data_contract.md."""
    today = today or dt.date.today()
    errors = []
    raw = payload.get("date_of_loss")
    if isinstance(raw, str):
        try:
            if dt.date.fromisoformat(raw) > today:
                errors.append({"field": "date_of_loss", "rule": "cross_field",
                               "message": "date_of_loss cannot be in the future"})
        except ValueError:
            pass  # the schema's date format check already reports this
    if payload.get("fir_lodged") is True and not payload.get("fir_number"):
        errors.append({"field": "fir_number", "rule": "cross_field",
                       "message": "fir_number is required when fir_lodged is true"})
    if payload.get("fir_lodged") is False and payload.get("fir_number"):
        errors.append({"field": "fir_number", "rule": "cross_field",
                       "message": "fir_number must be empty when fir_lodged is false"})
    return errors


def validate_fnol(payload, today: Optional[dt.date] = None) -> list:
    global _VALIDATOR
    if not isinstance(payload, dict):
        return [{"field": "$", "rule": "schema", "message": "request body must be a JSON object"}]
    if _VALIDATOR is None:
        _VALIDATOR = _load_validator()
    errors = [
        {"field": _field_of(e), "rule": e.validator, "message": e.message}
        for e in sorted(_VALIDATOR.iter_errors(payload), key=lambda e: list(e.path))
    ]
    errors += cross_field_errors(payload, today)
    return errors

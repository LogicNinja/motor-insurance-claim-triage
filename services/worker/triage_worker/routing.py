"""Routing is a business-rule layer. The policy wording defines no thresholds, so they live in YAML."""
import pathlib
import re

import yaml

_RULES = None
_OUT_OF_DOMAIN_CONTEXT = re.compile(
    r"\b(?:mars|martian|moon|lunar|outer space|space station|spacecraft|interplanetary)\b",
    re.IGNORECASE,
)


def is_out_of_corpus(description: str) -> bool:
    """Check that a loss is a motor-vehicle event within this policy corpus."""
    text = (description or "").casefold()
    if _OUT_OF_DOMAIN_CONTEXT.search(text):
        return True
    has_vehicle = any(k in text for k in ("car", "vehicle", "motorcycle", "motorbike", "scooter", "bike", "van", "truck", "registration", "driving", "road"))
    has_motor_loss = any(k in text for k in ("accident", "collision", "crash", "damage", "stolen", "theft", "hit", "loss"))
    return not (has_vehicle and has_motor_loss)


def _rules() -> dict:
    global _RULES
    if _RULES is None:
        path = pathlib.Path(__file__).with_name("routing_rules.yaml")
        _RULES = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _RULES


def _result(route: str, reason: str, escalated: bool, flags: list) -> dict:
    return {"route": route, "reason": reason, "escalated": escalated, "flags": flags}


def decide_route(claim: dict, coverage: dict) -> dict:
    rules = _rules()
    flags = list(coverage.get("flags", []))
    if coverage.get("out_of_corpus") or "out_of_corpus" in flags:
        return _result("human_review", "The reported loss is outside the motor policy corpus; a human must review.", True, flags)
    if coverage.get("position") == "escalate" or not coverage.get("verified"):
        return _result("human_review", "Evidence insufficient or unverified; a human must review.", True, flags)
    hit = [f for f in flags if f in rules["investigation_flags"]]
    if hit:
        return _result("investigation", "Flags requiring investigation: " + ", ".join(hit) + ".", False, flags)
    if claim.get("claim_type") in rules["investigation_claim_types"] or claim.get("third_party_injury"):
        return _result("investigation", "Theft or third-party injury needs investigation.", False, flags)
    amount = claim.get("estimated_damage_inr")
    if claim.get("claim_type") == "own_damage" and amount is not None and amount <= rules["fast_track_max_inr"]:
        return _result("fast_track", "Small own-damage claim with no flags.", False, flags)
    return _result("surveyor", "Assessment of the damage is needed.", False, flags)

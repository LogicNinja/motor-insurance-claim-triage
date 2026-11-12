from triage_worker.routing import decide_route
from triage_worker.routing import is_out_of_corpus


def covered(**kw):
    return dict({"position": "potentially_covered", "verified": True, "flags": []}, **kw)


def test_unverified_escalates():
    r = decide_route({}, {"position": "potentially_covered", "verified": False})
    assert r["route"] == "human_review" and r["escalated"] is True


def test_use_restriction_goes_to_investigation():
    r = decide_route({"claim_type": "own_damage"}, covered(position="potentially_not_covered", flags=["use_restriction"]))
    assert r["route"] == "investigation"


def test_small_own_damage_fast_track():
    r = decide_route({"claim_type": "own_damage", "estimated_damage_inr": 8000}, covered())
    assert r["route"] == "fast_track"


def test_default_is_surveyor():
    assert decide_route({"claim_type": "own_damage"}, covered())["route"] == "surveyor"


def test_ambiguous_use_restriction_routes_to_investigation():
    result = decide_route({"claim_type": "own_damage"}, covered(flags=["use_restriction_ambiguity"]))
    assert result["route"] == "investigation"
    assert result["escalated"] is False


def test_out_of_corpus_escalates_even_if_other_fields_look_verified():
    result = decide_route({"claim_type": "own_damage"}, covered(out_of_corpus=True))
    assert result["route"] == "human_review"
    assert result["escalated"] is True


def test_mars_vehicle_loss_is_out_of_policy_corpus():
    assert is_out_of_corpus("My car was damaged in an accident on Mars.") is True


def test_food_delivery_vehicle_accident_is_in_policy_corpus():
    assert is_out_of_corpus("I was making a food delivery run in my own car when the accident happened.") is False

from triage_worker.routing import decide_route


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

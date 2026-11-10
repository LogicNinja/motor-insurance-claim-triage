from triage_verifier.verify import verify

PASSAGES = [
    {"clause_id": "ch4", "page": 19, "text": "The Policy covers use for any purpose other than Hire or Reward."},
    {"clause_id": "s1a.1.vi", "page": 1, "text": "By accidental external means"},
]


def test_resolving_citations_pass():
    draft = {"position": "potentially_not_covered",
             "citations": [{"clause_id": "ch4", "quote": "other than hire   or reward"}]}
    assert verify(draft, PASSAGES)["verified"] is True


def test_unknown_clause_fails():
    draft = {"position": "potentially_covered", "citations": [{"clause_id": "ch99"}]}
    result = verify(draft, PASSAGES)
    assert result["verified"] is False and result["next_action"] == "search_again_or_escalate"


def test_invented_quote_fails():
    draft = {"position": "potentially_covered",
             "citations": [{"clause_id": "ch4", "quote": "food delivery is always covered"}]}
    assert verify(draft, PASSAGES)["verified"] is False


def test_position_without_citations_fails():
    assert verify({"position": "potentially_covered", "citations": []}, PASSAGES)["verified"] is False

from ingestion.chunk import extract_references


def test_extracts_named_reference():
    text = "Being used otherwise than in accordance with the \u2018Limitations as to Use\u2019 or"
    assert extract_references(text) == ["Limitations as to Use"]


def test_no_reference():
    assert extract_references("Any claim arising out of any contractual liability") == []

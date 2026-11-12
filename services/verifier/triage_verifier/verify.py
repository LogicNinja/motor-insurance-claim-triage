"""Citation verification. An unverified answer must never be returned (A3, F7)."""
import re


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def verify(draft: dict, passages: list) -> dict:
    """draft: {"position", "citations": [{"clause_id", "quote"}]}; passages: [{"clause_id", "text", "page"}]."""
    by_id: dict[str, list] = {}
    for passage in passages:
        clause_id = passage.get("clause_id")
        if clause_id is not None:
            by_id.setdefault(str(clause_id).casefold(), []).append(passage)
    problems = []
    citations = draft.get("citations", [])
    for c in citations:
        clause_id = str(c.get("clause_id", "")).casefold()
        matches = by_id.get(clause_id, [])
        if not matches:
            problems.append({"citation": c, "problem": "clause_id was not among the retrieved passages"})
            continue
        quote = _norm(c.get("quote", ""))
        if not quote:
            problems.append({"citation": c, "problem": "citation quote is required"})
        elif not any(quote in _norm(p.get("text", "")) for p in matches):
            problems.append({"citation": c, "problem": "quoted text not found in the retrieved passage"})
    if draft.get("position") in ("potentially_covered", "potentially_not_covered") and not citations:
        problems.append({"citation": None, "problem": "a coverage position requires at least one citation"})
    # TODO(A3): add an LLM check that each claim in the summary is supported by the cited passages.
    return {
        "verified": not problems,
        "problems": problems,
        "next_action": "pass" if not problems else "search_again_or_escalate",
    }

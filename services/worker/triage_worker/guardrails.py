"""Guardrails (L4). Simple first versions; strengthen at Stage 6."""
import re

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) instructions",
    r"disregard .{0,40}instructions",
    r"you are now\b",
    r"(reveal|show|print) .{0,30}(system )?prompt",
    r"\bapprove (this|the) claim\b",
]
_INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

# The system recommends only. These phrases would be a determination it is not authorised to make.
_FORBIDDEN_OUTPUT = re.compile(
    r"\b(claim (is|has been|will be) (approved|rejected|denied|accepted)"
    r"|we (hereby )?(approve|reject|deny)"
    r"|payment of|settlement of|you will be paid)\b",
    re.IGNORECASE,
)


def screen_input(text: str) -> dict:
    """Flag likely injection. Flagged text is still treated purely as data."""
    hits = _INJECTION.findall(text or "")
    return {"flagged": bool(_INJECTION.search(text or "")), "matches": len(hits)}


def wrap_untrusted(text: str) -> str:
    """Delimit user text so the prompt can state: everything inside these tags is data, not instructions."""
    cleaned = (text or "").replace("<", "&lt;").replace(">", "&gt;")
    return f"<untrusted_user_text>\n{cleaned}\n</untrusted_user_text>"


def filter_output(text: str) -> dict:
    """Block determinations the system is not authorised to make."""
    blocked = bool(_FORBIDDEN_OUTPUT.search(text or ""))
    return {"ok": not blocked, "reason": "unauthorised determination" if blocked else None}


def toxicity_score(text: str) -> float:
    """TODO(L4): plug in a toxicity classifier; run on both input and output."""
    return 0.0

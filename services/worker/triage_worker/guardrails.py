"""Guardrails (L4). Simple first versions; strengthen at Stage 6."""
import re

INJECTION_PATTERNS = [
    r"\b(?:ignore|disregard|forget|override)\b.{0,120}\b(?:instructions?|rules?|policies|guardrails)\b",
    r"you are now\b",
    r"\b(reveal|show|print|repeat|leak)\b.{0,60}\b(system\s+)?prompt\b",
    r"\bapprove (this|the) claim\b",
    r"\b(?:jailbreak|DAN mode|developer mode)\b",
    r"(?m:^\s*(?:system|assistant|developer)\s*:)",
]
_INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)
_INJECTION_SENTENCE = re.compile(
    r"(?i)(?:\b(?:ignore|disregard|forget|override)\b[^.!?\n]*(?:[.!?]|$)"
    r"|\b(?:you are now|reveal|show|print|repeat|leak)\b[^.!?\n]*(?:[.!?]|$)"
    r"|\bapprove\s+(?:this|the)\s+claim\b)"
)
_ROLE_PREFIX = re.compile(r"(?im)^\s*(?:system|assistant|developer)\s*:[^\n]*(?:\n|$)")

# The system recommends only. These phrases would be a determination it is not authorised to make.
_FORBIDDEN_OUTPUT = re.compile(
    r"\b(claim (is|has been|will be) (approved|rejected|denied|accepted)"
    r"|we (hereby )?(approve|reject|deny)"
    r"|payment of|settlement of|you will be paid)\b",
    re.IGNORECASE,
)


def screen_input(text: str) -> dict:
    """Flag instruction-like text before it reaches a prompt."""
    hits = list(_INJECTION.finditer(text or ""))
    return {"flagged": bool(hits), "matches": len(hits)}


def neutralize_injection(text: str) -> dict:
    """Redact directive-like spans while preserving ordinary claimant facts where possible."""
    original = text or ""
    screened = screen_input(original)
    if not screened["flagged"]:
        return {"text": original, "flagged": False, "redactions": 0}
    cleaned, redactions = _INJECTION_SENTENCE.subn("[instruction-like text redacted]", original)
    cleaned, role_redactions = _ROLE_PREFIX.subn("[role-like text redacted]\\n", cleaned)
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")
    return {"text": cleaned, "flagged": True, "redactions": redactions + role_redactions}


def wrap_untrusted(text: str) -> str:
    """Redact prompt injection, escape markup and delimit claimant-supplied data."""
    cleaned = neutralize_injection(text)["text"]
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")
    return f"<untrusted_user_text>\n{cleaned}\n</untrusted_user_text>"


def filter_output(text: str) -> dict:
    """Block determinations the system is not authorised to make."""
    blocked = bool(_FORBIDDEN_OUTPUT.search(text or ""))
    return {"ok": not blocked, "reason": "unauthorised determination" if blocked else None}


def toxicity_score(text: str) -> float:
    """Return the neutral placeholder score; toxicity classification is not enabled."""
    return 0.0

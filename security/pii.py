import re

PII_PATTERNS = [
    (re.compile(r"\b\d{13,19}\b"), "[REDACTED_CARD]"),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]?){2}\d{4}\b"), "[REDACTED_PHONE]"),
]


def mask_pii(text: str) -> str:
    masked = text
    for pattern, replacement in PII_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked

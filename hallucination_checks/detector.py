from __future__ import annotations

import re

from complaint_processing.preprocess import extract_metadata

PROMISE_PATTERNS = [
    (re.compile(r"\bguaranteed refund\b", re.I), "guaranteed_refund"),
    (re.compile(r"\bwe will refund\b", re.I), "unverified_refund_promise"),
    (re.compile(r"\bfull refund (is|has been) approved\b", re.I), "approved_refund_promise"),
    (re.compile(r"\bguaranteed compensation\b", re.I), "guaranteed_compensation"),
    (re.compile(r"\bwe will pay you\b", re.I), "payment_promise"),
    (re.compile(r"\bwill (arrive|be delivered) (today|tomorrow)\b", re.I), "unsupported_deadline"),
    (re.compile(r"\bpolicy exception (is|has been) approved\b", re.I), "unauthorized_exception"),
    (re.compile(r"\bwe (will|shall|have) (issue|process|approve)d? (you )?(a |your )?(full )?refund\b", re.I), "unverified_refund_promise"),
    (re.compile(r"\byour refund (is|has been) (approved|processed|issued)\b", re.I), "approved_refund_promise"),
    (re.compile(r"\b(free|immediate|guaranteed) replacement\b", re.I), "replacement_promise"),
    (re.compile(r"\bwe (will|shall) (send|ship) (you )?(a )?(new|replacement)\b", re.I), "replacement_promise"),
    (re.compile(r"\bwe (will|shall|have) (add|issue|give|credit)(ed)? (you )?(a )?(voucher|store credit|goodwill credit|discount|coupon)\b", re.I), "payment_promise"),
    (re.compile(r"\b(guarantee|promise) (that )?(it|your (order|parcel|package)) will (arrive|be delivered)\b", re.I), "unsupported_deadline"),
    (re.compile(r"\bmaking an exception\b|\bas a one[- ]time exception\b", re.I), "unauthorized_exception"),
]

REFUND_CODES = {"guaranteed_refund", "unverified_refund_promise", "approved_refund_promise"}
COMPENSATION_CODES = {"guaranteed_compensation", "payment_promise"}


def detect_unsupported_promises(response: str, python_result: dict) -> list[dict]:
    flags = []
    seen: set[str] = set()
    for pattern, code in PROMISE_PATTERNS:
        if code in seen or not pattern.search(response or ""):
            continue
        if code in REFUND_CODES:
            if python_result.get("refund_eligible") is not True:
                flags.append({"code": code, "detail": "Refund language is not supported by the rule matrix."})
        elif code in COMPENSATION_CODES:
            if not python_result.get("compensation_permitted"):
                flags.append({"code": code, "detail": "Compensation is not permitted by approved rules."})
        elif code == "replacement_promise":
            if python_result.get("replacement_eligible") is not True:
                flags.append({"code": code, "detail": "Replacement is not confirmed eligible by the rule matrix."})
        else:
            flags.append({"code": code, "detail": "Statement cannot be grounded in approved rules."})
        seen.add(code)
    return flags


def detect_hallucinations(
    output: dict, complaint_text: str, policy_chunks: list[dict], rule_output: dict | None = None
) -> list[dict]:
    """Flag generated facts that trace to neither the complaint, a policy excerpt, nor the rule matrix."""
    flags = []
    rule = rule_output or {}
    policy_text = "\n".join(chunk.get("content", "").lower() for chunk in policy_chunks)
    grounded = complaint_text.lower() + "\n" + policy_text
    grounded += " " + " ".join(str(v) for v in extract_metadata(complaint_text).values())
    rule_refs = {str(rule.get("policy_id") or ""), str(rule.get("policy_section") or "")} - {""}
    for key in ("policy_id", "policy_section"):
        value = str(output.get(key) or "").strip()
        if not value or value in rule_refs or value.lower() in grounded:
            continue
        if any(value == chunk.get("document_code") or value == chunk.get("section") for chunk in policy_chunks):
            continue
        flags.append({"code": "ungrounded_policy_reference", "field": key, "value": value})

    written = " ".join(
        str(output.get(key) or "") for key in ("customer_response", "follow_up_communication", "escalation_notes")
    )
    known_ids = {item.upper() for item in ID_PATTERN.findall(complaint_text + " " + policy_text)}
    known_ids |= {str(output.get("complaint_id") or "").upper()}
    for invented in sorted({item.upper() for item in ID_PATTERN.findall(written)} - known_ids):
        flags.append({"code": "invented_identifier", "value": invented})

    known_amounts = {_digits(a) for a in AMOUNT_PATTERN.findall(complaint_text + " " + policy_text)}
    for amount in AMOUNT_PATTERN.findall(written):
        if _digits(amount) not in known_amounts:
            flags.append({"code": "ungrounded_amount", "value": amount.strip()})
    return flags


ID_PATTERN = re.compile(r"\b(?:ORD|INV|TXN|NC|CMP)-\d{5,}\b", re.I)
AMOUNT_PATTERN = re.compile(r"(?:PKR|USD|Rs\.?|\$)\s?\d[\d,]*(?:\.\d+)?", re.I)


def _digits(value: str) -> str:
    return re.sub(r"[^\d.]", "", value)

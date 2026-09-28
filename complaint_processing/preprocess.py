from __future__ import annotations

import hashlib
import re
import unicodedata

MIN_COMPLAINT_CHARS = 20
ORDER_PATTERN = re.compile(r"^NC-\d{6,}$", re.I)
COMPLAINT_REF_PATTERN = re.compile(r"^CMP-\d{5,}$", re.I)


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).lower().encode("utf-8")).hexdigest()


def sanitize_input(text: str) -> str:
    cleaned = normalize_text(text)
    cleaned = cleaned.replace("<", " ").replace(">", " ")
    return cleaned


def extract_metadata(text: str) -> dict:
    amounts = re.findall(r"(?:PKR|USD|Rs\.?|\$)\s?\d[\d,]*", text, flags=re.I)
    dates = re.findall(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", text)
    order_ids = re.findall(r"\bNC-\d{6,}\b", text, flags=re.I)
    emails = re.findall(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", text)
    return {
        "amounts": amounts[:10],
        "dates": dates[:10],
        "order_ids": order_ids[:10],
        "emails": emails[:5],
    }


def validate_complaint_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    title = sanitize_input(payload.get("title") or "")
    description = sanitize_input(payload.get("description") or "")
    if not title:
        errors.append("Complaint title is required.")
    if not description:
        errors.append("Complaint description is required.")
    if description and len(description) < MIN_COMPLAINT_CHARS:
        errors.append("Complaint description is too short.")
    order_reference = (payload.get("order_reference") or "").strip()
    if order_reference and not ORDER_PATTERN.match(order_reference):
        errors.append("Invalid order reference. Expected format NC-000000.")
    previous = (payload.get("previous_complaint_reference") or "").strip()
    if previous and not COMPLAINT_REF_PATTERN.match(previous):
        errors.append("Invalid previous complaint reference. Expected format CMP-00000.")
    return errors

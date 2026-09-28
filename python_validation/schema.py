import json
import re

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from config.settings import ROOT_DIR
from schemas.intelligence import (
    ALLOWED_APPLICABILITY,
    ALLOWED_ESCALATION,
    ALLOWED_PRIORITY,
    ALLOWED_SENTIMENT,
    ALLOWED_URGENCY,
    IntelligenceOutput,
)

SCHEMA_PATH = ROOT_DIR / "schemas" / "complaint_intelligence.schema.json"
_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)


def extract_json(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output.")
    return json.loads(text[start : end + 1])


def coerce_enums(payload: dict) -> dict:
    """Fix letter case and spacing on enum fields ("Negative" -> "negative", "p1" -> "P1").

    Only formatting is repaired. A value outside the allowed set stays as returned so
    schema validation still reports it.
    """
    data = dict(payload)
    for key in ("sentiment", "urgency", "escalation_level", "policy_applicability"):
        if isinstance(data.get(key), str):
            data[key] = data[key].strip().lower().replace(" ", "_").replace("-", "_")
    if isinstance(data.get("priority"), str):
        data["priority"] = data["priority"].strip().upper()
    for key in ("escalation_required", "follow_up_required", "compensation_recommended"):
        if isinstance(data.get(key), str) and data[key].strip().lower() in {"true", "false"}:
            data[key] = data[key].strip().lower() == "true"
    return data


def structural_errors(payload: dict) -> list[str]:
    """JSON Schema errors only: missing required fields and wrong types or enum values."""
    return [f"{'/'.join(str(p) for p in e.path) or 'root'}: {e.message}" for e in _VALIDATOR.iter_errors(payload)]


def validate_schema(
    payload: dict,
    *,
    allowed_categories: set[str] | None = None,
    allowed_departments: set[str] | None = None,
    allowed_policies: set[str] | None = None,
) -> dict:
    errors = [e.message for e in _VALIDATOR.iter_errors(payload)]
    field_errors: list[str] = []
    try:
        IntelligenceOutput.model_validate(payload)
    except ValidationError as exc:
        field_errors.extend([err["msg"] for err in exc.errors()])

    sentiment = str(payload.get("sentiment", "")).lower()
    urgency = str(payload.get("urgency", "")).lower()
    priority = str(payload.get("priority", "")).upper()
    escalation_level = str(payload.get("escalation_level", "no_escalation")).lower()
    applicability = str(payload.get("policy_applicability", "applicable")).lower()

    if sentiment and sentiment not in ALLOWED_SENTIMENT:
        field_errors.append(f"Invalid sentiment: {sentiment}")
    if urgency and urgency not in ALLOWED_URGENCY:
        field_errors.append(f"Invalid urgency: {urgency}")
    if priority and priority not in ALLOWED_PRIORITY:
        field_errors.append(f"Invalid priority: {priority}")
    if escalation_level and escalation_level not in ALLOWED_ESCALATION:
        field_errors.append(f"Invalid escalation level: {escalation_level}")
    if applicability and applicability not in ALLOWED_APPLICABILITY:
        field_errors.append(f"Invalid policy applicability: {applicability}")
    if allowed_categories and payload.get("issue_category") not in allowed_categories:
        field_errors.append(f"Unknown category: {payload.get('issue_category')}")
    if allowed_departments and payload.get("department") not in allowed_departments:
        field_errors.append(f"Unknown department: {payload.get('department')}")
    policy_id = payload.get("policy_id")
    if allowed_policies and policy_id and policy_id not in allowed_policies:
        field_errors.append(f"Unknown policy_id: {policy_id}")

    all_errors = errors + field_errors
    return {"valid": not all_errors, "errors": all_errors, "normalized": _normalize(payload) if not all_errors else payload}


def _normalize(payload: dict) -> dict:
    data = dict(payload)
    data["sentiment"] = str(data.get("sentiment", "")).lower()
    data["urgency"] = str(data.get("urgency", "")).lower()
    data["priority"] = str(data.get("priority", "")).upper()
    data["escalation_level"] = str(data.get("escalation_level") or "no_escalation").lower()
    data["policy_applicability"] = str(data.get("policy_applicability") or "applicable").lower()
    return data

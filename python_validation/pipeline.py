from __future__ import annotations

import re

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from complaint_processing.preprocess import extract_metadata
from complaint_rules.engine import UNCLASSIFIED, classify_from_rules
from comparison_engine.compare import compare_outputs
from database.models import Complaint, ComplaintCategory, Department, PriorityRule
from escalation_rules.engine import evaluate_escalation
from hallucination_checks.detector import detect_hallucinations, detect_unsupported_promises
from knowledge_base.precedence import policy_status
from python_validation.schema import coerce_enums, validate_schema
from routing_rules.engine import recommend_departments
from security.prompt_injection import detect_prompt_injection

URGENCY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
PRIORITY_RANK = {"P3": 1, "P2": 2, "P1": 3, "P0": 4}
DEFAULT_PRIORITY = {"low": "P3", "medium": "P2", "high": "P1", "critical": "P0"}
SENSITIVE_CATEGORIES = {"safety", "privacy", "account"}
NEGATIONS = ("do not", "don't", "never", "avoid", "must not", "should not", "no ")
EVIDENCE_CATEGORIES = {"product defect", "warranty"}


def run_python_validation(
    db: Session,
    *,
    complaint: Complaint,
    genai_output: dict | None,
    policy_chunks: list[dict],
    allowed_categories: set[str],
    allowed_departments: set[str],
    allowed_policies: set[str],
    is_repeat: bool = False,
    open_count: int = 0,
    genai_failed: bool = False,
    genai_skipped_reason: str = "",
) -> dict:
    """Pipeline 2: derive the ground truth from rules, then check the GenAI draft against it.

    Nothing here calls a GenAI API, and GenAI output never changes the Python result.
    """
    text = f"{complaint.title}\n{complaint.description}"
    python_class = classify_from_rules(db, text)
    routing = recommend_departments(db, text, python_class)
    escalation = evaluate_escalation(
        db,
        text=text,
        category=python_class.get("issue_category", ""),
        customer_type=complaint.customer_type.value,
        is_repeat=is_repeat,
        open_count=open_count,
    )
    # Urgency comes from the rule matrix and escalation rules only; sentiment never sets it.
    urgency = python_class.get("urgency") or "medium"
    if escalation.get("force_urgency"):
        urgency = _max_urgency(urgency, escalation["force_urgency"])
    priority = _priority(
        db,
        urgency,
        rule_priority=python_class.get("priority"),
        vip=complaint.customer_type.value in {"vip", "enterprise"},
    )
    missing = detect_missing_information(complaint, python_class.get("issue_category"))
    rule_policy = policy_status(db, python_class.get("policy_id"))
    python_output = {
        "complaint_id": complaint.complaint_code,
        "issue_category": python_class.get("issue_category"),
        "subcategory": python_class.get("subcategory"),
        "secondary_issues": python_class.get("secondary_issues", []),
        "department": routing.get("primary_department"),
        "supporting_departments": routing.get("supporting_departments"),
        "urgency": urgency,
        "priority": priority,
        "policy_id": python_class.get("policy_id"),
        "policy_section": python_class.get("policy_section"),
        "policy_version": rule_policy["active_version"],
        "policy_applicability": rule_policy["applicability"] if python_class.get("policy_id") else "not_applicable",
        "escalation_required": bool(python_class.get("escalation_required") or escalation["escalation_required"]),
        "escalation_level": escalation["escalation_level"]
        if escalation["escalation_required"]
        else python_class.get("escalation_level", "no_escalation"),
        "escalation_reasons": escalation.get("reasons", []),
        "escalation_rules": escalation.get("matched_rules", []),
        "required_actions": python_class.get("required_actions", []),
        "prohibited_actions": python_class.get("prohibited_actions", []),
        "follow_up_required": python_class.get("follow_up_required", True),
        "refund_eligible": python_class.get("refund_eligible"),
        "replacement_eligible": python_class.get("replacement_eligible"),
        "compensation_permitted": python_class.get("compensation_permitted", False),
        "rule_code": python_class.get("rule_code"),
        "rule_matched": python_class.get("matched", False),
        "missing_information": missing,
        "entities": extract_metadata(text),
        "prompt_injection": detect_prompt_injection(
            f"{text}\n{complaint.requested_resolution or ''}"
        ),
    }
    if python_class.get("escalation_required") and not escalation["escalation_required"]:
        python_output["escalation_reasons"] = [f"Rule {python_class.get('rule_code')} mandates escalation."]
    if python_output["escalation_required"] and python_output["escalation_level"] == "no_escalation":
        python_output["escalation_level"] = "supervisor_review"

    flags: list[dict] = []
    reasons: list[str] = []
    schema_result: dict = {"valid": False, "errors": ["No GenAI output"]}
    comparison: dict = {}
    genai_policy: dict | None = None

    if genai_output:
        canonical = canonicalize_genai(db, genai_output)
        schema_result = validate_schema(
            canonical,
            allowed_categories=allowed_categories,
            allowed_departments=allowed_departments,
            allowed_policies=allowed_policies,
        )
        comparison = compare_outputs(canonical, python_output)
        response = canonical.get("customer_response") or ""
        flags.extend(detect_unsupported_promises(response, python_output))
        flags.extend(detect_hallucinations(canonical, text, policy_chunks, python_output))
        flags.extend(_resolution_flags(canonical, python_output))
        if canonical.get("compensation_recommended") and not python_output.get("compensation_permitted"):
            flags.append({"code": "unsupported_compensation", "detail": "Compensation is not permitted."})
        if canonical.get("refund_eligible") is True and python_output.get("refund_eligible") is False:
            flags.append({"code": "refund_not_eligible", "detail": "Rule matrix denies refund eligibility."})
        if canonical.get("replacement_eligible") is True and python_output.get("replacement_eligible") is False:
            flags.append({"code": "replacement_not_eligible", "detail": "Rule matrix denies replacement eligibility."})
        if python_output["escalation_required"] and not canonical.get("escalation_required"):
            flags.append({"code": "missed_mandatory_escalation", "detail": "; ".join(python_output["escalation_reasons"])})
        if not schema_result.get("valid"):
            reasons.append("GenAI output failed schema validation")
        if comparison.get("requires_manual_review"):
            reasons.append("GenAI and Python disagree significantly")

        cited = str(canonical.get("policy_id") or "").strip()
        if cited:
            genai_policy = policy_status(db, cited)
            if not genai_policy["known"]:
                flags.append({"code": "invalid_policy_id", "value": cited})
            elif genai_policy["applicability"] == "outdated":
                flags.append({"code": "outdated_policy", "value": cited, "detail": "Cited policy has no active version."})
            elif cited != python_output.get("policy_id"):
                genai_policy["applicability"] = "conditionally_applicable"
        elif python_output.get("policy_id"):
            flags.append({"code": "missing_policy_reference", "detail": "GenAI cited no approved policy."})
    elif genai_failed:
        flags.append({"code": "genai_failure", "detail": "Pipeline 1 returned no usable output after retries."})
        reasons.append("GenAI analysis failed; route to a human")

    if python_output["policy_id"] and not rule_policy["known"]:
        flags.append({"code": "rule_policy_missing", "value": python_output["policy_id"], "detail": "Rule cites a policy that is not in the knowledge base."})
        reasons.append("Policy support is missing")
    elif python_output["policy_applicability"] == "outdated":
        reasons.append("Rule policy has no active version")
    if _policy_conflict(policy_chunks):
        flags.append({"code": "policy_conflict", "detail": "Retrieved excerpts mix active and outdated versions of one policy."})
        reasons.append("Policy contradiction exists")

    injection = python_output["prompt_injection"]
    if injection.get("detected"):
        flags.append({"code": "prompt_injection", "patterns": injection.get("patterns")})
        reasons.append("Adversarial or manipulative content")
    if not python_class.get("matched"):
        flags.append({"code": "no_rule_match", "detail": "No rule in the matrix matched; classification needs a human."})
        reasons.append("Complaint is ambiguous")
    elif python_class.get("ambiguous"):
        reasons.append("Complaint is ambiguous")
    if python_class.get("configured_without_rule"):
        reasons.append("Category has no resolution rule yet")
    if python_output["escalation_required"]:
        reasons.append("Mandatory escalation")
    if (python_output["issue_category"] or "").lower() in SENSITIVE_CATEGORIES:
        reasons.append("Sensitive complaint")
    if missing:
        reasons.append("Missing information")
    score = None
    if comparison:
        score = float(comparison.get("verification_score") or 0)

    critical_blockers = (
        bool(injection.get("detected"))
        or (python_output.get("escalation_required") and python_output.get("escalation_level") in {"critical_management", "compliance_review"})
        or (python_output.get("issue_category") or "").lower() in SENSITIVE_CATEGORIES
        or not python_class.get("matched")
        or bool(comparison.get("requires_manual_review"))
        or not schema_result.get("valid")
    )

    if critical_blockers or (score is not None and score < 50) or not genai_output:
        requires_review = True
        if flags and not reasons:
            reasons.append("Validation flags raised")
    else:
        requires_review = False
        reasons = []
    checks = {
        "genai_available": bool(genai_output),
        "genai_skipped_reason": genai_skipped_reason,
        "schema": schema_result,
        "classification": {
            "rule_code": python_output["rule_code"],
            "matched": python_class.get("matched", False),
            "ambiguous": python_class.get("ambiguous", False),
            "match_score": python_class.get("match_score", 0),
        },
        "routing": {
            "python_department": python_output["department"],
            "supporting_departments": python_output["supporting_departments"],
        },
        "escalation": {
            "python_required": python_output["escalation_required"],
            "python_level": python_output["escalation_level"],
            "matched_rules": escalation.get("matched_rules", []),
        },
        "policy": {
            "rule_policy": {"code": python_output["policy_id"], **rule_policy},
            "genai_policy": genai_policy,
        },
        "policy_traceable": not any(f["code"] in {"invalid_policy_id", "ungrounded_policy_reference"} for f in flags),
        "sentiment_does_not_drive_urgency": True,
        "review_reasons": sorted(set(reasons)),
    }
    return {
        "python_output": python_output,
        "checks": checks,
        "flags": flags,
        "comparison": comparison,
        "verification_score": score,
        "requires_manual_review": requires_review,
    }


def canonicalize_genai(db: Session, genai_output: dict) -> dict:
    """Map department codes and case variants to the configured display names before comparing."""
    data = coerce_enums(genai_output)
    departments = db.query(Department).all()
    dept_alias = {d.code.lower(): d.name for d in departments} | {d.name.lower(): d.name for d in departments}
    category_alias = {c.name.lower(): c.name for c in db.query(ComplaintCategory).all()}
    if isinstance(data.get("department"), str):
        data["department"] = dept_alias.get(data["department"].strip().lower(), data["department"])
    if isinstance(data.get("supporting_departments"), list):
        data["supporting_departments"] = [dept_alias.get(str(d).strip().lower(), d) for d in data["supporting_departments"]]
    if isinstance(data.get("issue_category"), str):
        data["issue_category"] = category_alias.get(data["issue_category"].strip().lower(), data["issue_category"])
    return data


def detect_missing_information(complaint: Complaint, category: str | None = None) -> list[str]:
    missing = []
    text = f"{complaint.title} {complaint.description} {complaint.order_reference} {complaint.product_or_service}"
    if not complaint.order_reference and not re.search(r"\bNC-\d{6,}\b", text, re.I):
        missing.append("order_number")
    if not complaint.product_or_service.strip():
        missing.append("product")
    if len(complaint.description.strip()) < 40:
        missing.append("problem_description")
    if (category or "").lower() in EVIDENCE_CATEGORIES and not complaint.attachments:
        missing.append("evidence")
    return missing


def _resolution_flags(genai_output: dict, python_output: dict) -> list[dict]:
    """Mandatory actions must appear (paraphrase allowed); prohibited ones must not be offered."""
    flags = []
    steps = [str(s) for s in (genai_output.get("resolution_steps") or [])]
    guidance = [str(s) for s in (genai_output.get("agent_guidance") or [])]
    response = str(genai_output.get("customer_response") or "")
    candidates = steps + guidance + [response]
    for required in python_output.get("required_actions") or []:
        if not any(_similar(required, item) for item in candidates):
            flags.append({"code": "missing_mandatory_action", "action": required})
    offered = [s for s in steps if not s.strip().lower().startswith(NEGATIONS)] + [response]
    for prohibited in python_output.get("prohibited_actions") or []:
        if any(_similar(prohibited, item, threshold=85) and not _negated(prohibited, item) for item in offered):
            flags.append({"code": "prohibited_action", "action": prohibited})
    return flags


def _similar(action: str, text: str, threshold: int = 70) -> bool:
    action_l, text_l = action.lower(), text.lower()
    if action_l in text_l:
        return True
    # Short steps are compared word-set to word-set; long prose (the customer response)
    # is scanned for an aligned passage so unrelated sentences cannot dilute the score.
    if len(text_l) <= 3 * len(action_l):
        return fuzz.token_set_ratio(action_l, text_l) >= threshold
    return fuzz.partial_ratio(action_l, text_l) >= max(threshold, 85)


def _negated(action: str, text: str) -> bool:
    lowered = text.lower()
    index = lowered.find(action.lower().split()[0]) if action else -1
    window = lowered[max(0, index - 25) : index] if index >= 0 else ""
    return any(neg.strip() in window for neg in NEGATIONS)


def _policy_conflict(policy_chunks: list[dict]) -> bool:
    states: dict[str, set[bool]] = {}
    for chunk in policy_chunks:
        states.setdefault(chunk["document_code"], set()).add(bool(chunk.get("usable")))
    return any(len(values) > 1 for values in states.values())


def _priority(db: Session, urgency: str, *, rule_priority: str | None, vip: bool) -> str:
    """Urgency -> priority comes from the configurable priority_rules table.

    The result is never lower than the matched rule's own priority. VIP/enterprise
    customers get at least P2 but a minor VIP issue is not inflated to P1/P0.
    """
    mapping = {r.urgency.value: r.priority.value for r in db.query(PriorityRule).all()} or DEFAULT_PRIORITY
    priority = mapping.get(urgency, DEFAULT_PRIORITY.get(urgency, "P2"))
    if rule_priority and PRIORITY_RANK.get(rule_priority, 0) > PRIORITY_RANK.get(priority, 0):
        priority = rule_priority
    if vip and priority == "P3":
        priority = "P2"
    return priority


def _priority_from_urgency(urgency: str, vip: bool = False) -> str:
    priority = DEFAULT_PRIORITY.get(urgency, "P2")
    if vip and priority == "P3":
        return "P2"
    return priority


def _max_urgency(a: str, b: str) -> str:
    return a if URGENCY_RANK.get(a, 0) >= URGENCY_RANK.get(b, 0) else b


__all__ = ["UNCLASSIFIED", "run_python_validation", "detect_missing_information", "canonicalize_genai"]

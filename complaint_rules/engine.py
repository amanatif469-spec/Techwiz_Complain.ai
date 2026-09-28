from __future__ import annotations

from sqlalchemy.orm import Session

from complaint_rules.matching import first_position, keyword_score
from config.settings import get_settings
from database.models import ComplaintCategory, ComplaintSubcategory, Department, ResolutionRule

UNCLASSIFIED = "Unclassified"
URGENCY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def classify_from_rules(db: Session, text: str) -> dict:
    """Classify a complaint against the Complaint Resolution Rule Matrix.

    The best-scoring rule is the primary issue. A matched rule that mandates escalation
    (safety, privacy, account takeover) always wins over a higher keyword score so a
    critical issue is never routed as a secondary concern. Other matched categories
    become secondary issues and their departments become supporting departments.
    """
    categories = {c.code: c for c in db.query(ComplaintCategory).filter(ComplaintCategory.is_active.is_(True)).all()}
    subcategories = {
        (s.category.code, s.code): s
        for s in db.query(ComplaintSubcategory).filter(ComplaintSubcategory.is_active.is_(True)).all()
        if s.category
    }
    candidates = _rule_candidates(db, text, categories, subcategories)
    candidates.extend(_subcategory_candidates(text, categories, subcategories, candidates))
    matched = [c for c in candidates if c["match_score"] > 0]
    if not matched:
        return _unclassified(db)

    escalating = [c for c in matched if c["escalation_required"]]
    pool = escalating or matched
    # Ties go to the issue the customer mentions first, then to the more urgent rule.
    pool.sort(key=lambda c: (-c["match_score"], c["position"], -URGENCY_RANK.get(c["urgency"], 0), c["rule_code"] or ""))
    primary = pool[0]

    secondary: list[dict] = []
    seen = {primary["issue_category"]}
    for cand in sorted(matched, key=lambda c: (-c["match_score"], c["position"], c["rule_code"] or "")):
        if cand["issue_category"] in seen:
            continue
        seen.add(cand["issue_category"])
        secondary.append(
            {
                "issue_category": cand["issue_category"],
                "subcategory": cand["subcategory"],
                "department": cand["department"],
                "rule_code": cand["rule_code"],
            }
        )
        if len(secondary) == 3:
            break

    supporting = list(primary.get("supporting_departments") or [])
    for item in secondary:
        if item["department"] != primary["department"] and item["department"] not in supporting:
            supporting.append(item["department"])

    runner_up = next(
        (c for c in sorted(matched, key=lambda c: -c["match_score"]) if c["issue_category"] != primary["issue_category"]),
        None,
    )
    ambiguous = bool(
        not primary["escalation_required"]
        and runner_up
        and runner_up["match_score"] == primary["match_score"]
    )
    return {
        **primary,
        "supporting_departments": supporting,
        "secondary_issues": secondary,
        "matched": True,
        "ambiguous": ambiguous,
    }


def _rule_candidates(db: Session, text: str, categories: dict, subcategories: dict) -> list[dict]:
    out = []
    for rule in db.query(ResolutionRule).filter(ResolutionRule.is_active.is_(True)).all():
        keywords = (rule.conditions or {}).get("keywords", [])
        category = categories.get(rule.category_code)
        sub = subcategories.get((rule.category_code, rule.subcategory_code))
        out.append(
            {
                "issue_category": category.name if category else rule.category_code,
                "category_code": rule.category_code,
                "subcategory": sub.name if sub else rule.subcategory_code,
                "subcategory_code": rule.subcategory_code,
                "department": rule.department_code,
                "supporting_departments": list(rule.supporting_department_codes or []),
                "urgency": rule.urgency.value,
                "priority": rule.priority.value,
                "policy_id": rule.policy_code,
                "policy_section": rule.policy_section,
                "escalation_required": rule.escalation_required,
                "escalation_level": rule.escalation_level.value,
                "required_actions": rule.required_actions or [],
                "prohibited_actions": rule.prohibited_actions or [],
                "follow_up_required": rule.follow_up_required,
                "refund_eligible": rule.refund_eligible,
                "replacement_eligible": rule.replacement_eligible,
                "compensation_permitted": rule.compensation_permitted,
                "rule_code": rule.rule_code,
                "match_score": keyword_score(text, keywords),
                "position": first_position(text, keywords),
            }
        )
    return out


def _subcategory_candidates(text: str, categories: dict, subcategories: dict, rule_candidates: list[dict]) -> list[dict]:
    """Subcategory keywords classify categories that were configured without a rule yet.

    This is how a hidden/new category is processed through configuration alone. It only
    routes the complaint; with no rule there are no mandatory actions or eligibility, so
    the validation pipeline sends the case to manual review.
    """
    covered = {(c["category_code"], c["subcategory_code"]) for c in rule_candidates}
    out = []
    for (cat_code, sub_code), sub in subcategories.items():
        if (cat_code, sub_code) in covered:
            continue
        category = categories.get(cat_code)
        if not category:
            continue
        dept = category.default_department
        out.append(
            {
                "issue_category": category.name,
                "category_code": cat_code,
                "subcategory": sub.name,
                "subcategory_code": sub_code,
                "department": dept.code if dept else get_settings().default_department_code,
                "supporting_departments": [],
                "urgency": "medium",
                "priority": None,
                "policy_id": "",
                "policy_section": "",
                "escalation_required": False,
                "escalation_level": "no_escalation",
                "required_actions": [],
                "prohibited_actions": [],
                "follow_up_required": True,
                "refund_eligible": None,
                "replacement_eligible": None,
                "compensation_permitted": False,
                "rule_code": None,
                "configured_without_rule": True,
                "match_score": keyword_score(text, sub.keywords or []),
                "position": first_position(text, sub.keywords or []),
            }
        )
    return out


def _unclassified(db: Session) -> dict:
    """No rule matched: route to the configured default desk and let a human classify it."""
    code = get_settings().default_department_code
    dept = db.query(Department).filter(Department.code == code).first()
    return {
        "issue_category": UNCLASSIFIED,
        "subcategory": "Unspecified",
        "department": dept.code if dept else code,
        "supporting_departments": [],
        "secondary_issues": [],
        "urgency": "medium",
        "priority": None,
        "policy_id": "",
        "policy_section": "",
        "escalation_required": False,
        "escalation_level": "no_escalation",
        "required_actions": [],
        "prohibited_actions": [],
        "follow_up_required": True,
        "refund_eligible": None,
        "replacement_eligible": None,
        "compensation_permitted": False,
        "rule_code": None,
        "matched": False,
        "ambiguous": True,
        "match_score": 0,
    }

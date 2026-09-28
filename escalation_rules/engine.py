from __future__ import annotations

import re

from sqlalchemy.orm import Session

from complaint_processing.preprocess import extract_metadata
from complaint_rules.matching import keyword_hits
from config.settings import get_settings
from database.models import EscalationLevel, EscalationRule, UrgencyLevel

RANK = {
    EscalationLevel.no_escalation: 0,
    EscalationLevel.supervisor_review: 1,
    EscalationLevel.department_manager: 2,
    EscalationLevel.specialist_team: 3,
    EscalationLevel.compliance_review: 4,
    EscalationLevel.critical_management: 5,
}
URGENCY_RANK = {UrgencyLevel.low: 1, UrgencyLevel.medium: 2, UrgencyLevel.high: 3, UrgencyLevel.critical: 4}


def evaluate_escalation(
    db: Session,
    *,
    text: str,
    category: str = "",
    customer_type: str = "standard",
    is_repeat: bool = False,
    open_count: int = 0,
) -> dict:
    """Apply every active escalation rule independently of the GenAI output.

    A rule fires only when all of the conditions it specifies hold:
    keywords (any whole-word hit), categories, customer types, and repeat history.
    For repeat rules (``min_repeat_count`` > 0) the history alone is enough; a repeat
    keyword such as "third time" counts only when at least one related complaint is
    still open, so ordinary words like "again" cannot escalate a first complaint.
    """
    matched: list[dict] = []
    reasons: list[str] = []
    highest = EscalationLevel.no_escalation
    force_urgency: UrgencyLevel | None = None

    for rule in db.query(EscalationRule).filter(EscalationRule.is_active.is_(True)).all():
        hits = keyword_hits(text, rule.keywords or [])
        categories = [str(c).lower() for c in (rule.categories or [])]
        category_ok = not categories or (category or "").lower() in categories
        type_ok = not rule.customer_types or customer_type in rule.customer_types
        if rule.min_repeat_count:
            condition_ok = open_count >= rule.min_repeat_count or (bool(hits) and (is_repeat or open_count >= 1))
        elif rule.keywords:
            condition_ok = bool(hits)
        else:
            # Category- or customer-type-only rule, e.g. "every Privacy complaint escalates".
            condition_ok = bool(categories or rule.customer_types)
        if not (condition_ok and category_ok and type_ok):
            continue
        matched.append({"rule_code": rule.rule_code, "name": rule.name, "keywords": hits})
        reasons.append(rule.reason)
        if RANK[rule.escalation_level] > RANK[highest]:
            highest = rule.escalation_level
        if rule.force_urgency and (force_urgency is None or URGENCY_RANK[rule.force_urgency] > URGENCY_RANK[force_urgency]):
            force_urgency = rule.force_urgency

    amount = _largest_amount(text)
    threshold = get_settings().high_value_threshold
    if amount is not None and amount >= threshold:
        matched.append({"rule_code": "ESC-HIGH-VALUE", "name": "High-value dispute", "amount": amount})
        reasons.append(f"Disputed amount {amount:,.0f} meets the high-value threshold of {threshold:,.0f}.")
        if RANK[EscalationLevel.department_manager] > RANK[highest]:
            highest = EscalationLevel.department_manager
        if force_urgency is None or URGENCY_RANK[force_urgency] < URGENCY_RANK[UrgencyLevel.high]:
            force_urgency = UrgencyLevel.high

    return {
        "escalation_required": highest != EscalationLevel.no_escalation,
        "escalation_level": highest.value,
        "reasons": reasons,
        "matched_rules": matched,
        "force_urgency": force_urgency.value if force_urgency else None,
    }


def _largest_amount(text: str) -> float | None:
    values = []
    for raw in extract_metadata(text).get("amounts", []):
        digits = re.sub(r"[^\d.]", "", raw)
        try:
            values.append(float(digits))
        except ValueError:
            continue
    return max(values) if values else None

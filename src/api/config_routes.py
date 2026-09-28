from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import (
    ComplaintCategory,
    ComplaintSubcategory,
    CustomerType,
    Department,
    EscalationLevel,
    EscalationRule,
    KnowledgeDocument,
    PriorityCode,
    PriorityRule,
    ResolutionRule,
    SlaPolicy,
    UrgencyLevel,
)
from database.session import get_db
from genai_pipeline.client import paused_providers, provider_chain, reset_provider_pauses
from prompt_templates.loader import PROMPT_NAME, active_prompt_version, available_versions
from security.audit import write_audit
from security.auth import AdminUser, StaffUser
from src.api.schemas import (
    ActiveToggle,
    CategoryCreate,
    DepartmentCreate,
    EscalationRuleCreate,
    EscalationRuleUpdate,
    PriorityRuleUpdate,
    RuleCreate,
    SlaUpdate,
    SubcategoryCreate,
)

router = APIRouter(prefix="/api/v1/config", tags=["config"])


def _enum(enum_cls, value, field: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(e.value for e in enum_cls)
        raise HTTPException(status_code=422, detail=f"Invalid {field} '{value}'. Allowed: {allowed}.") from exc


@router.get("/departments")
def list_departments(user: StaffUser, db: Session = Depends(get_db)):
    return [
        {"id": d.id, "code": d.code, "name": d.name, "description": d.description, "is_active": d.is_active}
        for d in db.query(Department).order_by(Department.name).all()
    ]


@router.post("/departments")
def create_department(payload: DepartmentCreate, user: AdminUser, db: Session = Depends(get_db)):
    code = payload.code.strip().upper()
    if db.query(Department).filter((Department.code == code) | (Department.name == payload.name.strip())).first():
        raise HTTPException(status_code=409, detail="Department code or name already exists")
    row = Department(code=code, name=payload.name.strip(), description=payload.description)
    db.add(row)
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=code, action="create_department")
    db.commit()
    db.refresh(row)
    return {"id": row.id, "code": row.code, "name": row.name}


@router.get("/categories")
def list_categories(user: StaffUser, db: Session = Depends(get_db)):
    rows = db.query(ComplaintCategory).order_by(ComplaintCategory.name).all()
    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "is_active": c.is_active,
            "default_department_id": c.default_department_id,
            "default_department": c.default_department.name if c.default_department else None,
            "subcategories": [{"code": s.code, "name": s.name, "keywords": s.keywords} for s in c.subcategories],
        }
        for c in rows
    ]


@router.post("/categories")
def create_category(payload: CategoryCreate, user: AdminUser, db: Session = Depends(get_db)):
    dept = db.query(Department).filter(Department.code == payload.default_department_code.strip().upper()).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    code = payload.code.strip().upper()
    if db.query(ComplaintCategory).filter((ComplaintCategory.code == code) | (ComplaintCategory.name == payload.name.strip())).first():
        raise HTTPException(status_code=409, detail="Category code or name already exists")
    row = ComplaintCategory(code=code, name=payload.name.strip(), description=payload.description, default_department_id=dept.id)
    db.add(row)
    db.flush()
    for raw in payload.subcategories:
        sub = SubcategoryCreate.model_validate(raw)
        db.add(ComplaintSubcategory(category_id=row.id, code=sub.code.strip().upper(), name=sub.name.strip(), keywords=[k.strip().lower() for k in sub.keywords if k.strip()]))
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=code, action="create_category")
    db.commit()
    return {"id": row.id, "code": row.code}


@router.post("/categories/{category_code}/subcategories")
def add_subcategory(category_code: str, payload: SubcategoryCreate, user: AdminUser, db: Session = Depends(get_db)):
    category = db.query(ComplaintCategory).filter(ComplaintCategory.code == category_code.upper()).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    code = payload.code.strip().upper()
    if any(s.code == code for s in category.subcategories):
        raise HTTPException(status_code=409, detail="Subcategory already exists")
    db.add(ComplaintSubcategory(category_id=category.id, code=code, name=payload.name.strip(), keywords=[k.strip().lower() for k in payload.keywords if k.strip()]))
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=f"{category.code}/{code}", action="create_subcategory")
    db.commit()
    return {"category": category.code, "code": code}


@router.get("/rules")
def list_rules(user: StaffUser, db: Session = Depends(get_db)):
    return [
        {
            "id": r.id,
            "rule_code": r.rule_code,
            "category_code": r.category_code,
            "subcategory_code": r.subcategory_code,
            "department_code": r.department_code,
            "supporting_department_codes": r.supporting_department_codes,
            "urgency": r.urgency.value,
            "priority": r.priority.value,
            "escalation_required": r.escalation_required,
            "escalation_level": r.escalation_level.value,
            "policy_code": r.policy_code,
            "policy_section": r.policy_section,
            "conditions": r.conditions,
            "required_actions": r.required_actions,
            "prohibited_actions": r.prohibited_actions,
            "follow_up_required": r.follow_up_required,
            "refund_eligible": r.refund_eligible,
            "replacement_eligible": r.replacement_eligible,
            "compensation_permitted": r.compensation_permitted,
            "is_active": r.is_active,
        }
        for r in db.query(ResolutionRule).order_by(ResolutionRule.rule_code).all()
    ]


@router.post("/rules")
def create_rule(payload: RuleCreate, user: AdminUser, db: Session = Depends(get_db)):
    if db.query(ResolutionRule).filter(ResolutionRule.rule_code == payload.rule_code).first():
        raise HTTPException(status_code=409, detail="Rule exists")
    category = db.query(ComplaintCategory).filter(ComplaintCategory.code == payload.category_code).first()
    if not category:
        raise HTTPException(status_code=422, detail=f"Unknown category code {payload.category_code}")
    if not any(s.code == payload.subcategory_code for s in category.subcategories):
        raise HTTPException(status_code=422, detail=f"Unknown subcategory {payload.subcategory_code} for {payload.category_code}")
    for code in [payload.department_code, *payload.supporting_department_codes]:
        if not db.query(Department).filter(Department.code == code).first():
            raise HTTPException(status_code=422, detail=f"Unknown department code {code}")
    keywords = [k.strip().lower() for k in payload.keywords if k.strip()]
    if not keywords:
        raise HTTPException(status_code=422, detail="A rule needs at least one keyword condition.")
    warnings = []
    if payload.policy_code and not db.query(KnowledgeDocument).filter(KnowledgeDocument.document_code == payload.policy_code).first():
        warnings.append(f"Policy {payload.policy_code} is not in the knowledge base yet; complaints matching this rule will go to manual review.")
    row = ResolutionRule(
        rule_code=payload.rule_code,
        category_code=payload.category_code,
        subcategory_code=payload.subcategory_code,
        conditions={"keywords": keywords},
        department_code=payload.department_code,
        supporting_department_codes=payload.supporting_department_codes,
        urgency=_enum(UrgencyLevel, payload.urgency, "urgency"),
        priority=_enum(PriorityCode, payload.priority, "priority"),
        policy_code=payload.policy_code,
        policy_section=payload.policy_section,
        escalation_required=payload.escalation_required,
        escalation_level=_enum(EscalationLevel, payload.escalation_level, "escalation_level"),
        required_actions=payload.required_actions,
        prohibited_actions=payload.prohibited_actions,
        follow_up_required=payload.follow_up_required,
        refund_eligible=payload.refund_eligible,
        replacement_eligible=payload.replacement_eligible,
        compensation_permitted=payload.compensation_permitted,
    )
    db.add(row)
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=payload.rule_code, action="create_rule")
    db.commit()
    return {"id": row.id, "rule_code": row.rule_code, "warnings": warnings}


@router.patch("/rules/{rule_code}/active")
def toggle_rule(rule_code: str, payload: ActiveToggle, user: AdminUser, db: Session = Depends(get_db)):
    row = db.query(ResolutionRule).filter(ResolutionRule.rule_code == rule_code).first()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    row.is_active = payload.is_active
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=rule_code, action="toggle_rule", details={"is_active": payload.is_active})
    db.commit()
    return {"rule_code": rule_code, "is_active": row.is_active}


def _escalation_out(r: EscalationRule) -> dict:
    return {
        "rule_code": r.rule_code,
        "name": r.name,
        "keywords": r.keywords,
        "categories": r.categories,
        "customer_types": r.customer_types,
        "min_repeat_count": r.min_repeat_count,
        "escalation_level": r.escalation_level.value,
        "force_urgency": r.force_urgency.value if r.force_urgency else None,
        "reason": r.reason,
        "is_active": r.is_active,
    }


@router.get("/escalation-rules")
def list_escalation(user: StaffUser, db: Session = Depends(get_db)):
    return [_escalation_out(r) for r in db.query(EscalationRule).order_by(EscalationRule.rule_code).all()]


@router.post("/escalation-rules")
def create_escalation(payload: EscalationRuleCreate, user: AdminUser, db: Session = Depends(get_db)):
    if db.query(EscalationRule).filter(EscalationRule.rule_code == payload.rule_code).first():
        raise HTTPException(status_code=409, detail="Escalation rule exists")
    keywords = [k.strip().lower() for k in payload.keywords if k.strip()]
    if not (keywords or payload.categories or payload.customer_types or payload.min_repeat_count):
        raise HTTPException(status_code=422, detail="Give at least one condition: keywords, categories, customer types or a repeat count.")
    for ctype in payload.customer_types:
        _enum(CustomerType, ctype, "customer type")
    row = EscalationRule(
        rule_code=payload.rule_code,
        name=payload.name,
        keywords=keywords,
        categories=payload.categories,
        customer_types=payload.customer_types,
        min_repeat_count=payload.min_repeat_count,
        escalation_level=_enum(EscalationLevel, payload.escalation_level, "escalation_level"),
        reason=payload.reason,
        force_urgency=_enum(UrgencyLevel, payload.force_urgency, "urgency") if payload.force_urgency else None,
    )
    db.add(row)
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=payload.rule_code, action="create_escalation_rule")
    db.commit()
    return _escalation_out(row)


@router.patch("/escalation-rules/{rule_code}")
def update_escalation(rule_code: str, payload: EscalationRuleUpdate, user: AdminUser, db: Session = Depends(get_db)):
    row = db.query(EscalationRule).filter(EscalationRule.rule_code == rule_code).first()
    if not row:
        raise HTTPException(status_code=404, detail="Escalation rule not found")
    changes = payload.model_dump(exclude_unset=True)
    if "keywords" in changes:
        row.keywords = [k.strip().lower() for k in payload.keywords or [] if k.strip()]
    if "categories" in changes:
        row.categories = payload.categories or []
    if "min_repeat_count" in changes:
        row.min_repeat_count = payload.min_repeat_count or 0
    if "escalation_level" in changes:
        row.escalation_level = _enum(EscalationLevel, payload.escalation_level, "escalation_level")
    if "force_urgency" in changes:
        row.force_urgency = _enum(UrgencyLevel, payload.force_urgency, "urgency") if payload.force_urgency else None
    if "is_active" in changes:
        row.is_active = bool(payload.is_active)
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=rule_code, action="update_escalation_rule", details=changes)
    db.commit()
    return _escalation_out(row)


@router.get("/sla-policies")
def list_sla(user: StaffUser, db: Session = Depends(get_db)):
    return [
        {
            "code": s.code,
            "name": s.name,
            "customer_type": s.customer_type.value if s.customer_type else None,
            "priority": s.priority.value,
            "first_response_minutes": s.first_response_minutes,
            "resolution_hours": s.resolution_hours,
            "is_active": s.is_active,
        }
        for s in db.query(SlaPolicy).order_by(SlaPolicy.code).all()
    ]


@router.put("/sla-policies/{code}")
def update_sla(code: str, payload: SlaUpdate, user: AdminUser, db: Session = Depends(get_db)):
    row = db.query(SlaPolicy).filter(SlaPolicy.code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail="SLA policy not found")
    row.first_response_minutes = payload.first_response_minutes
    row.resolution_hours = payload.resolution_hours
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=code, action="update_sla", details=payload.model_dump())
    db.commit()
    return {"code": code, **payload.model_dump()}


@router.get("/priority-rules")
def list_priority(user: StaffUser, db: Session = Depends(get_db)):
    return [
        {"urgency": r.urgency.value, "priority": r.priority.value, "response_minutes": r.response_minutes, "resolution_hours": r.resolution_hours}
        for r in db.query(PriorityRule).order_by(PriorityRule.id).all()
    ]


@router.put("/priority-rules/{urgency}")
def update_priority(urgency: str, payload: PriorityRuleUpdate, user: AdminUser, db: Session = Depends(get_db)):
    level = _enum(UrgencyLevel, urgency, "urgency")
    row = db.query(PriorityRule).filter(PriorityRule.urgency == level).first()
    if not row:
        raise HTTPException(status_code=404, detail="Priority rule not found")
    row.priority = _enum(PriorityCode, payload.priority, "priority")
    write_audit(db, actor_id=user.id, entity_type="config", entity_id=f"priority:{urgency}", action="update_priority_rule", details=payload.model_dump())
    db.commit()
    return {"urgency": urgency, "priority": row.priority.value}


@router.get("/genai")
def genai_config(user: StaffUser):
    """Pipeline 1 configuration for the settings screen. Never returns key material."""
    settings = get_settings()
    chain = provider_chain(settings)
    models = {
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "anthropic": settings.anthropic_model,
        "grok": settings.grok_model,
    }
    return {
        "primary": settings.genai_provider,
        "chain": [{"provider": p, "model": models.get(p, "")} for p in chain],
        "configured": bool(chain),
        "paused": paused_providers(),
        "max_retries": settings.genai_max_retries,
        "timeout_seconds": settings.genai_timeout_seconds,
        "total_budget_seconds": settings.genai_total_budget_seconds,
        "prompt": {"name": PROMPT_NAME, "active_version": active_prompt_version(), "versions": available_versions()},
        "thresholds": {
            "high_value_threshold": settings.high_value_threshold,
            "repeat_similarity_threshold": settings.repeat_similarity_threshold,
            "default_department_code": settings.default_department_code,
        },
    }


@router.post("/genai/reset")
def reset_genai(user: AdminUser, db: Session = Depends(get_db)):
    """Retry paused providers immediately, e.g. after credits were added."""
    paused = paused_providers()
    reset_provider_pauses()
    write_audit(db, actor_id=user.id, entity_type="config", entity_id="genai", action="reset_provider_pauses", details={"paused": paused})
    db.commit()
    return {"reset": sorted(paused)}

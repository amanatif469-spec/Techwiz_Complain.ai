from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from config.settings import get_settings
from complaint_processing.duplicates import detect_repeat_unresolved, find_duplicates
from complaint_processing.preprocess import extract_metadata
from complaint_processing.sla import apply_sla, refresh_sla_risk
from comparison_engine.compare import compare_outputs
from database.models import (
    ComparisonResult,
    Complaint,
    ComplaintCategory,
    ComplaintStatus,
    Department,
    FollowUp,
    KnowledgeDocument,
    ValidationResult,
)
from genai_pipeline.pipeline import run_genai_pipeline
from knowledge_base.retrieval import retrieve_policy_chunks
from python_validation.pipeline import run_python_validation
from security.audit import write_audit

TONES = {"professional", "empathetic", "concise", "formal"}
# Once a human has moved a case on, re-analysis must not silently reopen it.
LATER_STATUSES = {ComplaintStatus.in_progress, ComplaintStatus.awaiting_customer, ComplaintStatus.resolved, ComplaintStatus.closed}


STATUS_MESSAGES = {
    ComplaintStatus.new: "We have received your complaint.",
    ComplaintStatus.analyzed: "Our team is reviewing your complaint.",
    ComplaintStatus.assigned: "Your complaint has been assigned to a support specialist.",
    ComplaintStatus.in_progress: "Our team is working on your complaint.",
    ComplaintStatus.awaiting_customer: "We need a little more information from you to continue.",
    ComplaintStatus.escalated: "Your complaint has been escalated for priority handling.",
    ComplaintStatus.resolved: "Your complaint has been resolved.",
    ComplaintStatus.closed: "Your complaint is closed.",
    ComplaintStatus.reopened: "Your complaint has been reopened and is being reviewed again.",
}


def customer_update(status: ComplaintStatus, department: str | None = None) -> str:
    """`latest_update` is shown to the customer, so it never carries internal notes or rule details."""
    message = STATUS_MESSAGES.get(status, "Your complaint is being handled.")
    if department and status in (ComplaintStatus.analyzed, ComplaintStatus.assigned, ComplaintStatus.in_progress, ComplaintStatus.escalated):
        message = message.rstrip(".") + f" by our {department} team."
    return message


def complaint_code_for(complaint_id: int) -> str:
    """Codes derive from the primary key, so they never drift from the id or get reused."""
    return f"CMP-{complaint_id:05d}"


def taxonomy_text(db: Session) -> str:
    lines = []
    for category in db.query(ComplaintCategory).filter(ComplaintCategory.is_active.is_(True)).order_by(ComplaintCategory.name):
        subs = ", ".join(s.name for s in category.subcategories if s.is_active) or "General"
        lines.append(f"- {category.name}: {subs}")
    return "\n".join(lines)


def analyze_complaint(db: Session, complaint: Complaint, *, tone: str = "professional", skip_genai: bool = False, actor_id: int | None = None) -> dict:
    if tone not in TONES:
        raise HTTPException(status_code=422, detail=f"Unknown tone '{tone}'. Use one of: {', '.join(sorted(TONES))}.")
    categories = [c.name for c in db.query(ComplaintCategory).filter(ComplaintCategory.is_active.is_(True)).all()]
    departments = [d.name for d in db.query(Department).filter(Department.is_active.is_(True)).all()]
    allowed_categories = set(categories)
    allowed_departments = set(departments) | {d.code for d in db.query(Department).all()}
    allowed_policies = {d.document_code for d in db.query(KnowledgeDocument).all()}

    settings = get_settings()
    repeat = detect_repeat_unresolved(db, complaint, similarity_threshold=settings.repeat_similarity_threshold)
    complaint.is_repeat = repeat["is_repeat"]
    repeat_context = (
        f"related unresolved complaints: {', '.join(repeat['related_codes'])}" if repeat["related_codes"] else "none found"
    )

    genai_output = None
    genai_run = None
    skipped_reason = ""
    policy_chunks: list[dict] = []
    if not skip_genai and not settings.has_any_genai_key():
        skip_genai = True
        skipped_reason = "No GenAI provider key is configured."
    elif skip_genai:
        skipped_reason = "Python-only validation requested."
    if not skip_genai:
        genai_run, policy_chunks = run_genai_pipeline(
            db,
            complaint,
            categories=categories,
            departments=departments,
            taxonomy=taxonomy_text(db),
            tone=tone,
            repeat_context=repeat_context,
        )
        genai_output = genai_run.structured_output or None
    else:
        policy_chunks = retrieve_policy_chunks(
            db, f"{complaint.title} {complaint.description} {complaint.product_or_service}"
        )

    validation = run_python_validation(
        db,
        complaint=complaint,
        genai_output=genai_output,
        policy_chunks=policy_chunks,
        allowed_categories=allowed_categories,
        allowed_departments=allowed_departments,
        allowed_policies=allowed_policies,
        is_repeat=repeat["is_repeat"],
        open_count=repeat["open_count"],
        genai_failed=bool(genai_run and genai_run.error_message),
        genai_skipped_reason=skipped_reason,
    )
    python_out = validation["python_output"]
    python_out["related_complaints"] = repeat["related_codes"]
    complaint.is_adversarial = bool(python_out.get("prompt_injection", {}).get("detected"))
    dept = (
        db.query(Department)
        .filter(or_(Department.name == python_out.get("department"), Department.code == python_out.get("department")))
        .first()
    )
    if dept:
        complaint.assigned_department_id = dept.id

    apply_sla(db, complaint, python_out.get("priority") or "P2")
    if python_out.get("follow_up_required"):
        follow_type, message = _follow_up(python_out, genai_output)
        complaint.follow_up_at = datetime.now(timezone.utc) + timedelta(days=1 if follow_type != "status_update" else 2)
        db.add(FollowUp(complaint_id=complaint.id, scheduled_at=complaint.follow_up_at, message=message, follow_up_type=follow_type))

    if complaint.status not in LATER_STATUSES:
        complaint.status = ComplaintStatus.escalated if python_out.get("escalation_required") else ComplaintStatus.analyzed
        complaint.latest_update = customer_update(complaint.status, dept.name if dept else None)

    result = ValidationResult(
        complaint_id=complaint.id,
        genai_run_id=genai_run.id if genai_run else None,
        python_output=python_out,
        checks=validation["checks"],
        flags=validation["flags"],
        verification_score=validation["verification_score"] or 0,
        requires_manual_review=validation["requires_manual_review"],
    )
    db.add(result)
    if genai_output:
        comparison_payload = validation.get("comparison") or compare_outputs(genai_output, python_out)
    else:
        comparison_payload = {
            "field_comparisons": {},
            "verification_status": "python_only",
            "explanation": skipped_reason or (genai_run.error_message if genai_run else ""),
        }
    comparison = ComparisonResult(
        complaint_id=complaint.id,
        field_comparisons=comparison_payload.get("field_comparisons", {}),
        match_count=comparison_payload.get("match_count", 0),
        mismatch_count=comparison_payload.get("mismatch_count", 0),
        verification_status=comparison_payload.get("verification_status", "manual_review"),
        explanation=comparison_payload.get("explanation", ""),
    )
    db.add(comparison)
    write_audit(
        db,
        actor_id=actor_id,
        entity_type="complaint",
        entity_id=complaint.complaint_code,
        action="analyze",
        details={
            "verification_status": comparison.verification_status,
            "score": validation["verification_score"],
            "genai_provider": genai_run.provider if genai_run else None,
            "prompt_version": genai_run.prompt_version if genai_run else None,
            "rule_code": python_out.get("rule_code"),
            "escalation_required": python_out.get("escalation_required"),
            "review_reasons": validation["checks"].get("review_reasons"),
        },
    )
    db.commit()
    db.refresh(complaint)
    return {
        "complaint": serialize_complaint(complaint),
        "genai": genai_output,
        "python": python_out,
        "comparison": comparison_payload,
        "flags": validation["flags"],
        "verification_score": validation["verification_score"],
        "requires_manual_review": result.requires_manual_review,
        "review_reasons": validation["checks"].get("review_reasons"),
        "metadata": extract_metadata(complaint.description),
        "prompt_injection": python_out.get("prompt_injection"),
        "policy_chunks": policy_chunks,
        "genai_error": genai_run.error_message if genai_run else "",
        "genai_skipped_reason": skipped_reason,
    }


def _follow_up(python_out: dict, genai_output: dict | None) -> tuple[str, str]:
    generated = (genai_output or {}).get("follow_up_communication") or ""
    if python_out.get("missing_information"):
        kind = "information_request"
        fallback = "Request missing details: " + ", ".join(python_out["missing_information"]).replace("_", " ") + "."
    elif python_out.get("escalation_required"):
        kind = "escalation_acknowledgement"
        fallback = "Confirm to the customer that the case has been escalated and share the next update time."
    elif python_out.get("refund_eligible"):
        kind = "refund_status_update"
        fallback = "Send a refund-status update once the refund is verified."
    elif python_out.get("replacement_eligible"):
        kind = "replacement_status_update"
        fallback = "Send a replacement-status update once eligibility is confirmed."
    else:
        kind = "status_update"
        fallback = "Follow up on resolution progress."
    return kind, generated or fallback


def latest_genai_output(complaint: Complaint):
    """Newest run that actually produced structured output, plus the newest run overall.

    A failed re-run stores an empty structured_output; without this the reviewer would
    lose a recommendation that was generated successfully on an earlier attempt.
    """
    runs = list(complaint.genai_runs or [])
    if not runs:
        return None, None
    with_output = [run for run in runs if run.structured_output]
    return (with_output[-1] if with_output else None), runs[-1]


def pending_review(complaint: Complaint) -> bool:
    """In the manual-review queue until a reviewer acts on the latest analysis."""
    latest_val = complaint.validation_results[-1] if complaint.validation_results else None
    if not latest_val or not latest_val.requires_manual_review:
        return False
    decisions = [r for r in complaint.reviews or [] if r.action.value != "comment"]
    return not any(r.created_at >= latest_val.created_at for r in decisions)


def serialize_complaint(complaint: Complaint, *, audience: str = "staff") -> dict:
    refresh_sla_risk(complaint)
    base = {
        "id": complaint.id,
        "complaint_code": complaint.complaint_code,
        "title": complaint.title,
        "description": complaint.description,
        "status": complaint.status.value,
        "product_or_service": complaint.product_or_service,
        "order_reference": complaint.order_reference,
        "previous_complaint_reference": complaint.previous_complaint_reference,
        "customer_type": complaint.customer_type.value,
        "channel": complaint.channel.value,
        "preferred_contact_channel": complaint.preferred_contact_channel,
        "requested_resolution": complaint.requested_resolution,
        "latest_update": complaint.latest_update,
        "sla_risk": complaint.sla_risk,
        "sla_resolution_due": complaint.sla_resolution_due,
        "follow_up_at": complaint.follow_up_at,
        "assigned_department_id": complaint.assigned_department_id,
        "department": complaint.assigned_department.name if complaint.assigned_department else None,
        "created_at": complaint.created_at,
        "updated_at": complaint.updated_at,
        "attachments": [{"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes} for a in complaint.attachments or []],
    }
    if audience == "customer":
        # Customers see tracking data only; drafts, rule output and flags are internal.
        return base

    genai_run, latest_genai = latest_genai_output(complaint)
    latest_val = complaint.validation_results[-1] if complaint.validation_results else None
    latest_cmp = complaint.comparisons[-1] if complaint.comparisons else None
    latest_review = next((r for r in reversed(complaint.reviews or [])), None)
    genai_available = bool(latest_val and (latest_val.checks or {}).get("genai_available", genai_run is not None))
    return {
        **base,
        "customer_code": complaint.customer.customer_code if complaint.customer else None,
        "is_repeat": complaint.is_repeat,
        "is_adversarial": complaint.is_adversarial,
        "duplicate_of": complaint.duplicate_of.complaint_code if complaint.duplicate_of else None,
        "assigned_to_id": complaint.assigned_to_id,
        "assigned_to": complaint.assigned_to.full_name if complaint.assigned_to else None,
        "genai": genai_run.structured_output if genai_run else None,
        "genai_meta": {
            "provider": (genai_run or latest_genai).provider,
            "model": (genai_run or latest_genai).model,
            "prompt_version": (genai_run or latest_genai).prompt_version,
            "attempt": (genai_run or latest_genai).attempt,
            "latency_ms": (genai_run or latest_genai).latency_ms,
            "policy_versions": (genai_run or latest_genai).policy_versions,
            "analyzed_at": (genai_run or latest_genai).created_at,
            "error": latest_genai.error_message,
            "available": genai_run is not None,
            "stale": genai_run is not None and genai_run.id != latest_genai.id,
        }
        if latest_genai
        else None,
        "python": latest_val.python_output if latest_val else None,
        "checks": latest_val.checks if latest_val else None,
        "flags": latest_val.flags if latest_val else [],
        "verification_score": latest_val.verification_score if latest_val and genai_available else None,
        "requires_manual_review": latest_val.requires_manual_review if latest_val else None,
        "pending_review": pending_review(complaint),
        "analyzed_at": latest_val.created_at if latest_val else None,
        "comparison": {
            "fields": latest_cmp.field_comparisons,
            "status": latest_cmp.verification_status,
            "explanation": latest_cmp.explanation,
        }
        if latest_cmp
        else None,
        "open_followups": [
            {"type": f.follow_up_type, "message": f.message, "scheduled_at": f.scheduled_at}
            for f in sorted(complaint.followups or [], key=lambda f: f.id)
            if not f.completed
        ],
        "latest_review": {
            "action": latest_review.action.value,
            "comments": latest_review.comments,
            "final_decision": latest_review.final_decision,
            "created_at": latest_review.created_at,
        }
        if latest_review
        else None,
    }


def ensure_not_duplicate_block(db: Session, text: str, customer_id: int | None) -> dict:
    found = find_duplicates(db, text=text, customer_id=customer_id)
    if found.get("exact"):
        raise HTTPException(
            status_code=409,
            detail=f"Exact duplicate of {found['match_code']}",
        )
    return found

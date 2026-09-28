import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from complaint_processing.preprocess import content_hash, sanitize_input, validate_complaint_payload
from config.settings import get_settings
from database.models import (
    AuditLog,
    Complaint,
    ComplaintAttachment,
    ComplaintStatus,
    Customer,
    Department,
    FollowUp,
    ReviewAction,
    ReviewActionType,
    User,
    UserRole,
)
from database.session import get_db
from document_processing.validate import safe_filename, validate_complaint_attachment
from security.audit import write_audit
from security.auth import CurrentUser, ReviewerUser, StaffUser
from src.api.schemas import AnalyzeRequest, AssignRequest, ComplaintCreate, CustomerDecision, ReviewRequest, StatusUpdate
from src.services.analysis import (
    analyze_complaint,
    ensure_not_duplicate_block,
    complaint_code_for,
    customer_update,
    pending_review,
    serialize_complaint,
)

router = APIRouter(prefix="/api/v1/complaints", tags=["complaints"])


def _customer_for_user(db: Session, user: User) -> Customer | None:
    return db.query(Customer).filter(Customer.user_id == user.id).first()


def _audience(user: User) -> str:
    return "customer" if user.role == UserRole.customer else "staff"


def _loaders():
    """selectin loading keeps sibling collections from multiplying into a cartesian product."""
    return (
        selectinload(Complaint.genai_runs),
        selectinload(Complaint.validation_results),
        selectinload(Complaint.comparisons),
        selectinload(Complaint.reviews),
        selectinload(Complaint.attachments),
        selectinload(Complaint.assigned_department),
        selectinload(Complaint.assigned_to),
        selectinload(Complaint.customer),
        selectinload(Complaint.duplicate_of),
        selectinload(Complaint.followups),
    )


def _get_complaint(db: Session, complaint_id: int) -> Complaint:
    complaint = db.query(Complaint).options(*_loaders()).filter(Complaint.id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return complaint


@router.post("")
def submit_complaint(
    payload: ComplaintCreate,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    errors = validate_complaint_payload(payload.model_dump())
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    title = sanitize_input(payload.title)
    description = sanitize_input(payload.description)
    customer = _customer_for_user(db, user)
    if user.role == UserRole.customer and not customer:
        raise HTTPException(status_code=400, detail="Customer profile is missing.")
    if user.role != UserRole.customer and payload.customer_code:
        customer = db.query(Customer).filter(Customer.customer_code == payload.customer_code.strip()).first()
        if not customer:
            raise HTTPException(status_code=422, detail=[f"Unknown customer reference {payload.customer_code}."])
    previous = payload.previous_complaint_reference.strip().upper()
    if previous:
        cited = db.query(Complaint).filter(Complaint.complaint_code == previous).first()
        if not cited or (customer and cited.customer_id not in (None, customer.id)):
            raise HTTPException(status_code=422, detail=[f"Previous complaint {previous} was not found for this customer."])
    # A customer cannot promote themselves to VIP; the type comes from the profile.
    customer_type = customer.customer_type if customer else payload.customer_type
    duplicate = ensure_not_duplicate_block(db, f"{title}\n{description}", customer.id if customer else None)
    complaint = Complaint(
        # Placeholder until the insert assigns an id; replaced before commit.
        complaint_code=f"TMP-{uuid.uuid4().hex[:24]}",
        customer_id=customer.id if customer else None,
        submitted_by_id=user.id,
        title=title,
        description=description,
        normalized_text=f"{title} {description}".lower(),
        content_hash=content_hash(f"{title}\n{description}"),
        product_or_service=sanitize_input(payload.product_or_service),
        order_reference=payload.order_reference.strip().upper(),
        previous_complaint_reference=previous,
        customer_type=customer_type,
        preferred_contact_channel=sanitize_input(payload.preferred_contact_channel) or "email",
        requested_resolution=sanitize_input(payload.requested_resolution),
        duplicate_of_id=duplicate.get("match_id"),
    )
    db.add(complaint)
    db.flush()
    complaint.complaint_code = complaint_code_for(complaint.id)
    write_audit(db, actor_id=user.id, entity_type="complaint", entity_id=complaint.complaint_code, action="submit")
    db.commit()
    complaint = _get_complaint(db, complaint.id)
    return {
        "complaint": serialize_complaint(complaint, audience=_audience(user)),
        "near_duplicate": duplicate.get("near_duplicate"),
        "near_duplicate_of": duplicate.get("match_code"),
    }


@router.post("/{complaint_id}/attachments")
def add_attachment(
    complaint_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    complaint = _get_visible_complaint(db, user, complaint_id)
    content = file.file.read()
    validate_complaint_attachment(file, content)
    dest_dir = get_settings().upload_path / "complaints" / str(complaint.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    original = safe_filename(file.filename or "", "attachment.bin")
    dest = dest_dir / f"{uuid.uuid4().hex[:8]}_{original}"
    dest.write_bytes(content)
    row = ComplaintAttachment(
        complaint_id=complaint.id,
        filename=original,
        content_type=file.content_type or "application/octet-stream",
        storage_path=str(dest),
        size_bytes=len(content),
    )
    db.add(row)
    write_audit(db, actor_id=user.id, entity_type="complaint", entity_id=complaint.complaint_code, action="attachment", details={"filename": original})
    db.commit()
    return {"id": row.id, "filename": row.filename, "size_bytes": row.size_bytes}


@router.get("")
def list_complaints(
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = None,
    category: str | None = None,
    department: str | None = None,
    priority: str | None = None,
    urgency: str | None = None,
    sentiment: str | None = None,
    q: str | None = None,
    sla_risk: bool | None = None,
    escalated: bool | None = None,
    review: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 200,
):
    query = db.query(Complaint).options(*_loaders())
    if user.role == UserRole.customer:
        customer = _customer_for_user(db, user)
        query = query.filter(Complaint.customer_id == (customer.id if customer else -1))
    elif user.role == UserRole.agent:
        query = query.filter(or_(Complaint.assigned_to_id == user.id, Complaint.assigned_to_id.is_(None)))
    if status:
        try:
            query = query.filter(Complaint.status == ComplaintStatus(status))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Unknown status '{status}'") from exc
    if date_from:
        query = query.filter(Complaint.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        query = query.filter(Complaint.created_at <= datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    if q:
        like = f"%{q.strip()}%"
        query = query.outerjoin(Customer, Complaint.customer_id == Customer.id).filter(
            or_(
                Complaint.complaint_code.ilike(like),
                Complaint.title.ilike(like),
                Complaint.order_reference.ilike(like),
                Complaint.product_or_service.ilike(like),
                Customer.customer_code.ilike(like),
            )
        )
    rows = query.order_by(Complaint.id.desc()).limit(max(1, min(limit, 1000))).all()
    audience = _audience(user)
    payload = []
    for row in rows:
        item = serialize_complaint(row, audience=audience)
        python = item.get("python") or {}
        genai = item.get("genai") or {}
        if sla_risk is True and not item["sla_risk"]:
            continue
        if audience == "staff":
            if category and python.get("issue_category") != category:
                continue
            if department and python.get("department") != department:
                continue
            if priority and python.get("priority") != priority:
                continue
            if urgency and python.get("urgency") != urgency:
                continue
            if sentiment and str(genai.get("sentiment") or "").lower() != sentiment.lower():
                continue
            if escalated is True and not (row.status == ComplaintStatus.escalated or python.get("escalation_required")):
                continue
            if escalated is False and (row.status == ComplaintStatus.escalated or python.get("escalation_required")):
                continue
            if review is True and not item.get("pending_review"):
                continue
        payload.append(item)
    return payload


@router.get("/queue/manual-review")
def manual_review_queue(user: ReviewerUser, db: Session = Depends(get_db)):
    rows = db.query(Complaint).options(*_loaders()).order_by(Complaint.id.desc()).all()
    return [serialize_complaint(row) for row in rows if pending_review(row)]


@router.get("/{complaint_id}")
def get_complaint(complaint_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    complaint = _get_visible_complaint(db, user, complaint_id)
    return serialize_complaint(complaint, audience=_audience(user))


@router.get("/{complaint_id}/history")
def complaint_history(complaint_id: int, user: StaffUser, db: Session = Depends(get_db)):
    """Audit trail: every action plus the original recommendation next to each reviewer decision."""
    complaint = _get_visible_complaint(db, user, complaint_id)
    actors = {u.id: u.full_name for u in db.query(User).all()}
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "complaint", AuditLog.entity_id == complaint.complaint_code)
        .order_by(AuditLog.id)
        .all()
    )
    return {
        "audit": [
            {"at": a.created_at, "actor": actors.get(a.actor_id, "system"), "action": a.action, "details": a.details}
            for a in audit
        ],
        "reviews": [
            {
                "at": r.created_at,
                "reviewer": actors.get(r.reviewer_id),
                "action": r.action.value,
                "comments": r.comments,
                "original_recommendation": r.original_recommendation,
                "final_decision": r.final_decision,
            }
            for r in complaint.reviews
        ],
        "genai_runs": [
            {
                "at": r.created_at,
                "provider": r.provider,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "attempt": r.attempt,
                "latency_ms": r.latency_ms,
                "valid": r.is_valid_schema,
                "error": r.error_message,
                "policy_versions": r.policy_versions,
            }
            for r in complaint.genai_runs
        ],
        "followups": [
            {"scheduled_at": f.scheduled_at, "type": f.follow_up_type, "message": f.message, "completed": f.completed}
            for f in sorted(complaint.followups, key=lambda f: f.id)
        ],
    }


@router.post("/{complaint_id}/analyze")
def analyze(complaint_id: int, user: StaffUser, payload: AnalyzeRequest, db: Session = Depends(get_db)):
    complaint = _get_visible_complaint(db, user, complaint_id)
    return analyze_complaint(db, complaint, tone=payload.tone, skip_genai=payload.skip_genai, actor_id=user.id)


@router.patch("/{complaint_id}/status")
def update_status(complaint_id: int, payload: StatusUpdate, user: StaffUser, db: Session = Depends(get_db)):
    complaint = _get_visible_complaint(db, user, complaint_id)
    try:
        new_status = ComplaintStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown status '{payload.status}'") from exc
    previous = complaint.status.value
    complaint.status = new_status
    # A typed note is written for the customer (the UI says so); otherwise use standard wording.
    complaint.latest_update = sanitize_input(payload.note) or customer_update(new_status)
    if new_status in (ComplaintStatus.resolved, ComplaintStatus.closed):
        _settle_followups(db, complaint, confirm_resolution=new_status == ComplaintStatus.resolved)
    write_audit(
        db,
        actor_id=user.id,
        entity_type="complaint",
        entity_id=complaint.complaint_code,
        action="status",
        details={"from": previous, "to": new_status.value, "note": payload.note},
    )
    db.commit()
    return serialize_complaint(_get_complaint(db, complaint_id))


@router.post("/{complaint_id}/assign")
def assign(complaint_id: int, payload: AssignRequest, user: StaffUser, db: Session = Depends(get_db)):
    complaint = _get_visible_complaint(db, user, complaint_id)
    details = {}
    if payload.agent_id:
        agent = db.query(User).filter(User.id == payload.agent_id, User.is_active.is_(True)).first()
        if not agent or agent.role == UserRole.customer:
            raise HTTPException(status_code=422, detail="Assignee must be an active staff user.")
        if user.role == UserRole.agent and agent.id != user.id:
            raise HTTPException(status_code=403, detail="Agents can only assign complaints to themselves.")
        complaint.assigned_to_id = agent.id
        details["agent"] = agent.full_name
    if payload.department_id:
        if not db.query(Department).filter(Department.id == payload.department_id).first():
            raise HTTPException(status_code=422, detail="Unknown department.")
        complaint.assigned_department_id = payload.department_id
        details["department_id"] = payload.department_id
    if not details:
        raise HTTPException(status_code=422, detail="Provide agent_id or department_id.")
    # Taking ownership must not hide an escalation or undo work already in progress.
    if complaint.status in (ComplaintStatus.new, ComplaintStatus.analyzed, ComplaintStatus.reopened):
        complaint.status = ComplaintStatus.assigned
    dept_name = db.query(Department).filter(Department.id == complaint.assigned_department_id).first()
    complaint.latest_update = customer_update(complaint.status, dept_name.name if dept_name else None)
    write_audit(db, actor_id=user.id, entity_type="complaint", entity_id=complaint.complaint_code, action="assign", details=details)
    db.commit()
    return serialize_complaint(_get_complaint(db, complaint_id))


@router.post("/{complaint_id}/review")
def review_complaint(complaint_id: int, payload: ReviewRequest, user: ReviewerUser, db: Session = Depends(get_db)):
    complaint = _get_complaint(db, complaint_id)
    try:
        action = ReviewActionType(payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unknown review action") from exc
    latest_val = complaint.validation_results[-1] if complaint.validation_results else None
    genai = next((r.structured_output for r in reversed(complaint.genai_runs) if r.structured_output), {})
    # Both pipelines' outputs are preserved as the original recommendation.
    original = {"genai": genai, "python": latest_val.python_output if latest_val else {}}
    decision = dict(payload.final_decision or {})
    department_id = decision.get("department_id")
    if department_id is not None:
        dept = db.query(Department).filter(Department.id == int(department_id)).first()
        if not dept:
            raise HTTPException(status_code=422, detail="Unknown department.")
        decision["department"] = dept.name
    if action in {ReviewActionType.reassign, ReviewActionType.reclassify} and not decision:
        raise HTTPException(status_code=422, detail=f"{action.value} needs a final decision (e.g. department_id or issue_category).")
    if action == ReviewActionType.modify and not decision and not payload.comments:
        raise HTTPException(status_code=422, detail="Describe the modification in comments or final_decision.")

    review = ReviewAction(
        complaint_id=complaint.id,
        reviewer_id=user.id,
        action=action,
        original_recommendation=original,
        final_decision=decision or {"accepted": "python" if action == ReviewActionType.approve else None},
        comments=payload.comments,
    )
    db.add(review)
    # Reviewer comments are internal: they go to the review record and audit log only.
    if action == ReviewActionType.approve:
        complaint.status = ComplaintStatus.in_progress
    elif action == ReviewActionType.reject:
        complaint.status = ComplaintStatus.analyzed
    elif action == ReviewActionType.escalate:
        complaint.status = ComplaintStatus.escalated
    elif action in {ReviewActionType.reassign, ReviewActionType.reclassify, ReviewActionType.modify}:
        if department_id is not None:
            complaint.assigned_department_id = int(department_id)
        complaint.status = ComplaintStatus.assigned if action == ReviewActionType.reassign else ComplaintStatus.in_progress
    if action not in (ReviewActionType.comment, ReviewActionType.regenerate):
        dept = db.query(Department).filter(Department.id == complaint.assigned_department_id).first()
        complaint.latest_update = customer_update(complaint.status, dept.name if dept else None)
    write_audit(
        db,
        actor_id=user.id,
        entity_type="complaint",
        entity_id=complaint.complaint_code,
        action=f"review_{action.value}",
        details={"original": original, "final": review.final_decision, "comments": payload.comments},
    )
    db.commit()
    if action == ReviewActionType.regenerate:
        return analyze_complaint(db, _get_complaint(db, complaint_id), tone=decision.get("tone", "professional"), actor_id=user.id)["complaint"]
    return serialize_complaint(_get_complaint(db, complaint_id))


@router.post("/{complaint_id}/customer-decision")
def customer_decision(complaint_id: int, payload: CustomerDecision, user: CurrentUser, db: Session = Depends(get_db)):
    """The customer confirms a resolution (closing it) or reopens it because the fix failed."""
    if user.role != UserRole.customer:
        raise HTTPException(status_code=403, detail="Only the customer who raised the complaint can confirm or reopen it.")
    complaint = _get_visible_complaint(db, user, complaint_id)
    if complaint.status != ComplaintStatus.resolved:
        raise HTTPException(status_code=409, detail="Only a resolved complaint can be confirmed or reopened.")
    comment = sanitize_input(payload.comment)
    if payload.action == "confirm":
        complaint.status = ComplaintStatus.closed
        complaint.latest_update = "Thank you for confirming. Your complaint is closed."
        _settle_followups(db, complaint, confirm_resolution=False)
    elif payload.action == "reopen":
        if len(comment) < 10:
            raise HTTPException(status_code=422, detail="Please tell us briefly what is still wrong (at least 10 characters).")
        complaint.status = ComplaintStatus.reopened
        complaint.is_repeat = True
        complaint.latest_update = customer_update(ComplaintStatus.reopened)
        _settle_followups(db, complaint, confirm_resolution=False)
        # Keep the customer's reason in front of the owning agent as an open action item.
        complaint.follow_up_at = datetime.now(timezone.utc)
        db.add(FollowUp(complaint_id=complaint.id, scheduled_at=complaint.follow_up_at, message=comment, follow_up_type="customer_reopened"))
    else:
        raise HTTPException(status_code=422, detail="action must be 'confirm' or 'reopen'")
    write_audit(
        db,
        actor_id=user.id,
        entity_type="complaint",
        entity_id=complaint.complaint_code,
        action=f"customer_{payload.action}",
        details={"comment": comment},
    )
    db.commit()
    return serialize_complaint(_get_complaint(db, complaint_id), audience="customer")


def _settle_followups(db: Session, complaint: Complaint, *, confirm_resolution: bool) -> None:
    """Pending follow-ups are done once a case is resolved; a resolution check-in replaces them."""
    for followup in db.query(FollowUp).filter(FollowUp.complaint_id == complaint.id, FollowUp.completed.is_(False)).all():
        followup.completed = True
    if confirm_resolution:
        complaint.follow_up_at = datetime.now(timezone.utc) + timedelta(days=2)
        db.add(
            FollowUp(
                complaint_id=complaint.id,
                scheduled_at=complaint.follow_up_at,
                message="Check the resolution worked for the customer, then close the complaint.",
                follow_up_type="resolution_confirmation",
            )
        )
    else:
        complaint.follow_up_at = None


def _get_visible_complaint(db: Session, user: User, complaint_id: int) -> Complaint:
    complaint = _get_complaint(db, complaint_id)
    if user.role == UserRole.customer:
        customer = _customer_for_user(db, user)
        if not customer or complaint.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="Not allowed")
    elif user.role == UserRole.agent and complaint.assigned_to_id not in (None, user.id):
        # Agents work their own queue: unassigned cases or cases assigned to them.
        raise HTTPException(status_code=403, detail="This complaint is assigned to another agent.")
    return complaint


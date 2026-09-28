from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from database.models import Complaint, ComplaintStatus, CustomerType, PriorityCode, SlaPolicy


def apply_sla(db: Session, complaint: Complaint, priority: str) -> None:
    now = datetime.now(timezone.utc)
    policy = (
        db.query(SlaPolicy)
        .filter(
            SlaPolicy.is_active.is_(True),
            SlaPolicy.priority == PriorityCode(priority),
        )
        .order_by(SlaPolicy.customer_type.is_(None))
        .first()
    )
    if complaint.customer_type != CustomerType.standard:
        typed = (
            db.query(SlaPolicy)
            .filter(
                SlaPolicy.is_active.is_(True),
                SlaPolicy.priority == PriorityCode(priority),
                SlaPolicy.customer_type == complaint.customer_type,
            )
            .first()
        )
        policy = typed or policy
    if not policy:
        response_minutes = {"P0": 30, "P1": 120, "P2": 480, "P3": 1440}.get(priority, 480)
        resolution_hours = {"P0": 4, "P1": 24, "P2": 72, "P3": 168}.get(priority, 72)
        risk_pct = 75
    else:
        response_minutes = policy.first_response_minutes
        resolution_hours = policy.resolution_hours
        risk_pct = policy.risk_threshold_percent
    complaint.sla_first_response_due = now + timedelta(minutes=response_minutes)
    complaint.sla_resolution_due = now + timedelta(hours=resolution_hours)
    elapsed_ratio = 0.0
    complaint.sla_risk = elapsed_ratio >= (risk_pct / 100)


def refresh_sla_risk(complaint: Complaint, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    due = complaint.sla_resolution_due
    start = complaint.created_at.replace(tzinfo=timezone.utc) if complaint.created_at.tzinfo is None else complaint.created_at
    # A finished complaint cannot breach its SLA any more.
    if not due or complaint.status in (ComplaintStatus.resolved, ComplaintStatus.closed):
        complaint.sla_risk = False
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    total = (due - start).total_seconds()
    used = (now - start).total_seconds()
    complaint.sla_risk = total > 0 and (used / total) >= 0.75
    return complaint.sla_risk

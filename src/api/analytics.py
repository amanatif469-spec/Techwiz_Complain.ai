import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from complaint_processing.sla import refresh_sla_risk
from database.models import Complaint, ComplaintStatus, Customer, UserRole
from database.session import get_db
from security.auth import AdminUser, CurrentUser, ManagerUser, StaffUser
from src.services.analysis import latest_genai_output, pending_review

router = APIRouter(prefix="/api/v1", tags=["analytics"])

CLOSED = {ComplaintStatus.resolved, ComplaintStatus.closed}
REPORTS = {
    "complaints": "Complaint analysis",
    "comparison": "GenAI / Python comparison",
    "escalations": "Escalations",
    "sla": "SLA status",
    "manual_review": "Manual reviews",
    "departments": "Department performance",
    "policy_usage": "Policy usage",
    "resolution_compliance": "Resolution compliance",
}


def _all_complaints(db: Session) -> list[Complaint]:
    rows = (
        db.query(Complaint)
        .options(
            selectinload(Complaint.validation_results),
            selectinload(Complaint.comparisons),
            selectinload(Complaint.genai_runs),
            selectinload(Complaint.reviews),
            selectinload(Complaint.assigned_department),
            selectinload(Complaint.customer),
        )
        .order_by(Complaint.id)
        .all()
    )
    for row in rows:
        refresh_sla_risk(row)
    return rows


def _latest(row: Complaint) -> tuple[dict, dict, object]:
    genai_run, _ = latest_genai_output(row)
    python = row.validation_results[-1].python_output if row.validation_results else {}
    return (python or {}), (genai_run.structured_output if genai_run else {}), row.validation_results[-1] if row.validation_results else None


@router.get("/dashboards/admin")
def admin_dashboard(user: AdminUser, db: Session = Depends(get_db)):
    return _metrics(_all_complaints(db))


@router.get("/dashboards/agent")
def agent_dashboard(user: StaffUser, db: Session = Depends(get_db)):
    rows = _all_complaints(db)
    if user.role == UserRole.agent:
        rows = [r for r in rows if r.assigned_to_id in {None, user.id}]
    open_rows = [r for r in rows if r.status not in CLOSED]
    return {
        "assigned": [_brief(r) for r in reversed(open_rows) if r.assigned_to_id == user.id],
        "queue": [_brief(r) for r in reversed(open_rows) if r.status in {ComplaintStatus.new, ComplaintStatus.analyzed, ComplaintStatus.reopened} and r.assigned_to_id is None],
        "escalation_warnings": [_brief(r) for r in reversed(open_rows) if r.status == ComplaintStatus.escalated or _latest(r)[0].get("escalation_required")],
        "sla_risks": [_brief(r) for r in reversed(open_rows) if r.sla_risk],
    }


@router.get("/dashboards/customer")
def customer_dashboard(user: CurrentUser, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.user_id == user.id).first()
    if user.role == UserRole.customer and not customer:
        return []
    query = db.query(Complaint).options(selectinload(Complaint.assigned_department))
    if user.role == UserRole.customer:
        query = query.filter(Complaint.customer_id == customer.id)
    rows = query.order_by(Complaint.id.desc()).limit(100).all()
    return [
        {
            "id": row.id,
            "complaint_code": row.complaint_code,
            "title": row.title,
            "status": row.status.value,
            "submitted_date": row.created_at,
            "department": row.assigned_department.name if row.assigned_department else None,
            "latest_update": row.latest_update,
            "resolution_status": "resolved" if row.status in CLOSED else "open",
        }
        for row in rows
    ]


@router.get("/analytics")
def analytics(user: ManagerUser, db: Session = Depends(get_db)):
    return _metrics(_all_complaints(db))


@router.get("/analytics/trends")
def trends(user: ManagerUser, db: Session = Depends(get_db), days: int = 7):
    """Compare the latest window with the one before it to spot rising categories and escalation spikes."""
    rows = _all_complaints(db)
    now = datetime.now(timezone.utc)
    window = timedelta(days=max(1, days))
    by_day: dict[str, Counter] = defaultdict(Counter)
    escalations_by_day: Counter = Counter()
    current, previous = Counter(), Counter()
    products_current: Counter = Counter()
    for row in rows:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        python, _, _ = _latest(row)
        category = python.get("issue_category") or "Unanalyzed"
        day = created.date().isoformat()
        by_day[day][category] += 1
        if python.get("escalation_required") or row.status == ComplaintStatus.escalated:
            escalations_by_day[day] += 1
        if created >= now - window:
            current[category] += 1
            if row.product_or_service:
                products_current[(row.product_or_service, category)] += 1
        elif created >= now - 2 * window:
            previous[category] += 1
    rising = []
    for category, count in current.items():
        before = previous.get(category, 0)
        if count >= 3 and count >= 1.5 * max(before, 1):
            rising.append({"category": category, "current": count, "previous": before, "note": f"Rising {category.lower()} complaints"})
    recurring = [
        {"product": product, "category": category, "count": count, "note": "Recurring product issue"}
        for (product, category), count in products_current.most_common()
        if count >= 3
    ]
    daily_escalations = list(escalations_by_day.values())
    average = sum(daily_escalations) / len(daily_escalations) if daily_escalations else 0
    spikes = [
        {"date": day, "escalations": count, "note": "Escalation spike"}
        for day, count in sorted(escalations_by_day.items())
        if average and count >= 2 * average and count >= 3
    ]
    repeat_failures = sum(1 for r in rows if r.is_repeat and r.created_at and (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)) >= now - window)
    return {
        "window_days": window.days,
        "daily": {day: dict(counter) for day, counter in sorted(by_day.items())},
        "rising_categories": sorted(rising, key=lambda r: -r["current"]),
        "recurring_product_issues": recurring[:10],
        "escalation_spikes": spikes,
        "repeated_service_failures": repeat_failures,
    }


@router.get("/reports")
def list_reports(user: ManagerUser):
    return [{"key": key, "name": name} for key, name in REPORTS.items()]


@router.get("/reports/export")
def export_report(user: ManagerUser, db: Session = Depends(get_db), fmt: str = "csv", report: str = "complaints"):
    if report not in REPORTS:
        raise HTTPException(status_code=422, detail=f"Unknown report '{report}'. Use one of: {', '.join(REPORTS)}.")
    if fmt not in {"csv", "xlsx", "pdf"}:
        raise HTTPException(status_code=422, detail="fmt must be csv, xlsx or pdf")
    records = _report_rows(db, report)
    filename = f"supportnova-{report}.{fmt}"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    columns = list(records[0].keys()) if records else ["complaint_code"]
    if fmt == "xlsx":
        import pandas as pd

        buffer = BytesIO()
        pd.DataFrame(records, columns=columns).to_excel(buffer, index=False, sheet_name=report[:31])
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
    if fmt == "pdf":
        return StreamingResponse(_pdf(REPORTS[report], columns, records), media_type="application/pdf", headers=headers)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(records)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)


def _pdf(title: str, columns: list[str], records: list[dict]) -> BytesIO:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell", fontSize=6.5, leading=8)
    head = styles["BodyText"].clone("head", fontSize=7, leading=8, textColor=colors.white)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data = [[Paragraph(c.replace("_", " "), head) for c in columns]]
    for record in records[:500]:
        data.append([Paragraph(str(record.get(c, ""))[:160].replace("&", "&amp;").replace("<", "&lt;"), cell) for c in columns])
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d4d4d8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f5")]),
            ]
        )
    )
    story = [
        Paragraph(f"SupportNova — {title}", styles["Title"]),
        Paragraph(f"Generated {generated} · {len(records)} rows" + (" (first 500 shown)" if len(records) > 500 else ""), styles["Normal"]),
        Spacer(1, 8),
        table if records else Paragraph("No data.", styles["Normal"]),
    ]
    doc.build(story)
    buffer.seek(0)
    return buffer


def _report_rows(db: Session, report: str) -> list[dict]:
    rows = _all_complaints(db)
    if report == "departments":
        return _department_rows(rows)
    if report == "policy_usage":
        return _policy_rows(rows)
    if report == "resolution_compliance":
        return _compliance_rows(rows)
    out = []
    for row in rows:
        python, genai, val = _latest(row)
        cmp = row.comparisons[-1] if row.comparisons else None
        base = {"complaint_code": row.complaint_code, "created_at": row.created_at.isoformat() if row.created_at else "", "status": row.status.value}
        if report == "complaints":
            out.append(
                {
                    **base,
                    "customer": row.customer.customer_code if row.customer else "",
                    "product": row.product_or_service,
                    "category": python.get("issue_category", ""),
                    "subcategory": python.get("subcategory", ""),
                    "department": python.get("department", ""),
                    "urgency": python.get("urgency", ""),
                    "priority": python.get("priority", ""),
                    "sentiment": genai.get("sentiment", ""),
                    "escalation": python.get("escalation_required", ""),
                    "sla_risk": row.sla_risk,
                    "repeat": row.is_repeat,
                    "verification_status": cmp.verification_status if cmp else "",
                    "verification_score": val.verification_score if val and (val.checks or {}).get("genai_available", True) else "",
                }
            )
        elif report == "comparison" and val:
            fields = cmp.field_comparisons if cmp else {}
            out.append(
                {
                    "complaint_code": row.complaint_code,
                    "genai_category": genai.get("issue_category", ""),
                    "python_category": python.get("issue_category", ""),
                    "genai_department": genai.get("department", ""),
                    "python_department": python.get("department", ""),
                    "genai_urgency": genai.get("urgency", ""),
                    "python_urgency": python.get("urgency", ""),
                    "genai_escalation": genai.get("escalation_required", ""),
                    "python_escalation": python.get("escalation_required", ""),
                    "genai_policy": genai.get("policy_id", ""),
                    "python_policy": python.get("policy_id", ""),
                    "match": "match" if fields and all(f.get("match") for f in fields.values()) else ("mismatch" if fields else "no GenAI output"),
                    "verification_status": cmp.verification_status if cmp else "",
                    "explanation": cmp.explanation if cmp else "",
                }
            )
        elif report == "escalations" and (python.get("escalation_required") or row.status == ComplaintStatus.escalated):
            out.append(
                {
                    **base,
                    "category": python.get("issue_category", ""),
                    "department": python.get("department", ""),
                    "level": python.get("escalation_level", ""),
                    "reasons": "; ".join(python.get("escalation_reasons") or []),
                    "genai_flagged": genai.get("escalation_required", ""),
                }
            )
        elif report == "sla" and row.sla_resolution_due:
            out.append(
                {
                    **base,
                    "priority": python.get("priority", ""),
                    "resolution_due": row.sla_resolution_due.isoformat(),
                    "at_risk": row.sla_risk,
                    "breached": row.status not in CLOSED and row.sla_resolution_due < datetime.now(timezone.utc),
                }
            )
        elif report == "manual_review" and val and val.requires_manual_review:
            review = row.reviews[-1] if row.reviews else None
            out.append(
                {
                    **base,
                    "pending": pending_review(row),
                    "reasons": "; ".join((val.checks or {}).get("review_reasons") or []),
                    "flags": ", ".join(f.get("code", "") for f in val.flags or []),
                    "reviewer_action": review.action.value if review else "",
                    "reviewer_comments": review.comments if review else "",
                }
            )
    return out


def _department_rows(rows: list[Complaint]) -> list[dict]:
    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "open": 0, "resolved": 0, "escalated": 0, "sla_risk": 0, "hours": []})
    for row in rows:
        name = row.assigned_department.name if row.assigned_department else "Unassigned"
        s = stats[name]
        s["total"] += 1
        s["resolved" if row.status in CLOSED else "open"] += 1
        s["escalated"] += row.status == ComplaintStatus.escalated
        s["sla_risk"] += bool(row.sla_risk)
        hours = _resolution_hours(row)
        if hours is not None:
            s["hours"].append(hours)
    return [
        {
            "department": name,
            "total": s["total"],
            "open": s["open"],
            "resolved": s["resolved"],
            "escalated": s["escalated"],
            "sla_risk": s["sla_risk"],
            "avg_resolution_hours": round(sum(s["hours"]) / len(s["hours"]), 1) if s["hours"] else "",
        }
        for name, s in sorted(stats.items())
    ]


def _policy_rows(rows: list[Complaint]) -> list[dict]:
    usage: Counter = Counter()
    genai_usage: Counter = Counter()
    for row in rows:
        python, genai, _ = _latest(row)
        if python.get("policy_id"):
            usage[python["policy_id"]] += 1
        if genai.get("policy_id"):
            genai_usage[genai["policy_id"]] += 1
    codes = sorted(set(usage) | set(genai_usage))
    return [{"policy": code, "rule_matrix_citations": usage.get(code, 0), "genai_citations": genai_usage.get(code, 0)} for code in codes]


def _compliance_rows(rows: list[Complaint]) -> list[dict]:
    out = []
    for row in rows:
        python, genai, val = _latest(row)
        if not val or not genai:
            continue
        codes = [f.get("code", "") for f in val.flags or []]
        out.append(
            {
                "complaint_code": row.complaint_code,
                "rule_code": python.get("rule_code", ""),
                "missing_mandatory_actions": codes.count("missing_mandatory_action"),
                "prohibited_actions": codes.count("prohibited_action"),
                "unsupported_promises": sum(c in {"guaranteed_refund", "unverified_refund_promise", "approved_refund_promise", "payment_promise", "replacement_promise", "unsupported_deadline", "unauthorized_exception", "unsupported_compensation"} for c in codes),
                "ungrounded_claims": sum(c in {"ungrounded_policy_reference", "invented_identifier", "ungrounded_amount"} for c in codes),
                "compliant": not codes,
            }
        )
    return out


def _resolution_hours(row: Complaint) -> float | None:
    if row.status not in CLOSED or not row.updated_at or not row.created_at:
        return None
    return (row.updated_at - row.created_at).total_seconds() / 3600


def _metrics(rows: list[Complaint]) -> dict:
    categories, departments, priorities, urgencies, sentiments, products = (Counter() for _ in range(6))
    statuses = Counter(r.status.value for r in rows)
    mismatches = reviews = pending = escalations = repeats = sla_risks = analyzed = compared = verified = 0
    scores: list[float] = []
    hours: list[float] = []
    for row in rows:
        python, genai, val = _latest(row)
        if val:
            analyzed += 1
        categories[python.get("issue_category") or "Unanalyzed"] += 1
        departments[python.get("department") or "Unassigned"] += 1
        priorities[python.get("priority") or "n/a"] += 1
        urgencies[python.get("urgency") or "n/a"] += 1
        sentiments[str(genai.get("sentiment") or "n/a").lower()] += 1
        if row.product_or_service:
            products[row.product_or_service] += 1
        cmp = row.comparisons[-1] if row.comparisons else None
        if cmp and cmp.field_comparisons:
            compared += 1
            verified += cmp.verification_status == "verified"
            mismatches += bool(cmp.mismatch_count)
            if val:
                scores.append(val.verification_score)
        if val and val.requires_manual_review:
            reviews += 1
        pending += pending_review(row)
        escalations += bool(row.status == ComplaintStatus.escalated or python.get("escalation_required"))
        repeats += bool(row.is_repeat)
        sla_risks += bool(row.sla_risk)
        h = _resolution_hours(row)
        if h is not None:
            hours.append(h)
    open_with_sla = [r for r in rows if r.sla_resolution_due and r.status not in CLOSED]
    return {
        "total": len(rows),
        "analyzed": analyzed,
        "statuses": dict(statuses),
        "categories": dict(categories),
        "departments": dict(departments),
        "priorities": dict(priorities),
        "urgencies": dict(urgencies),
        "sentiments": dict(sentiments),
        "products": dict(products.most_common(10)),
        "escalations": escalations,
        "sla_risks": sla_risks,
        "genai_python_mismatches": mismatches,
        "genai_compared": compared,
        "verified_matches": verified,
        # None rather than a made-up number when nothing has been compared yet.
        "agreement_rate": round(100 * verified / compared, 1) if compared else None,
        "average_verification_score": round(sum(scores) / len(scores), 1) if scores else None,
        "sla_compliance": round(100 * (1 - sum(r.sla_risk for r in open_with_sla) / len(open_with_sla)), 1) if open_with_sla else None,
        "average_resolution_hours": round(sum(hours) / len(hours), 1) if hours else None,
        "manual_review_cases": reviews,
        "pending_reviews": pending,
        "repeat_complaints": repeats,
    }


def _brief(row: Complaint) -> dict:
    python, genai, val = _latest(row)
    return {
        "id": row.id,
        "complaint_code": row.complaint_code,
        "title": row.title,
        "status": row.status.value,
        "created_at": row.created_at,
        "category": python.get("issue_category"),
        "priority": python.get("priority"),
        "sentiment": genai.get("sentiment"),
        "genai_recommendation": genai.get("complaint_summary") or genai.get("primary_issue"),
        "validation_status": row.comparisons[-1].verification_status if row.comparisons else "pending",
        "verification_score": val.verification_score if val and (val.checks or {}).get("genai_available", True) else None,
        "escalation": python.get("escalation_required"),
        "escalation_level": python.get("escalation_level"),
        "sla_risk": row.sla_risk,
        "suggested_response": genai.get("customer_response"),
    }


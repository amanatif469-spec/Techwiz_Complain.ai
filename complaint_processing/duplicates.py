from sqlalchemy import or_
from sqlalchemy.orm import Session
from rapidfuzz import fuzz

from complaint_processing.preprocess import content_hash, normalize_text
from database.models import Complaint, ComplaintStatus


NEAR_DUPLICATE_THRESHOLD = 88


def find_duplicates(db: Session, *, text: str, customer_id: int | None) -> dict:
    digest = content_hash(text)
    exact = (
        db.query(Complaint)
        .filter(Complaint.content_hash == digest)
        .order_by(Complaint.id.desc())
        .first()
    )
    if exact:
        return {"exact": True, "near_duplicate": False, "match_id": exact.id, "match_code": exact.complaint_code}

    candidates_query = db.query(Complaint)
    if customer_id:
        candidates_query = candidates_query.filter(Complaint.customer_id == customer_id)
    candidates = candidates_query.order_by(Complaint.id.desc()).limit(50).all()
    normalized = normalize_text(text).lower()
    best = None
    best_score = 0
    for candidate in candidates:
        score = fuzz.token_set_ratio(normalized, candidate.normalized_text.lower())
        if score > best_score:
            best_score = score
            best = candidate
    if best and best_score >= NEAR_DUPLICATE_THRESHOLD:
        return {
            "exact": False,
            "near_duplicate": True,
            "score": best_score,
            "match_id": best.id,
            "match_code": best.complaint_code,
        }
    return {"exact": False, "near_duplicate": False, "match_id": None}


OPEN_STATUSES = [
    ComplaintStatus.new,
    ComplaintStatus.analyzed,
    ComplaintStatus.assigned,
    ComplaintStatus.in_progress,
    ComplaintStatus.awaiting_customer,
    ComplaintStatus.escalated,
    ComplaintStatus.reopened,
]


def detect_repeat_unresolved(db: Session, complaint: Complaint, *, similarity_threshold: int = 55) -> dict:
    """Find earlier complaints this one repeats (SRS steps 53-54, repeat challenge 13).

    A complaint is related to an earlier one when the customer cites it as the previous
    complaint (any status: a resolved case raised again means the resolution failed), or
    when the same customer has another unresolved complaint about the same order or with
    similar wording. The complaint being analyzed is never counted against itself.
    """
    related: dict[int, Complaint] = {}
    reference = (complaint.previous_complaint_reference or "").strip().upper()
    if reference:
        cited = db.query(Complaint).filter(Complaint.complaint_code == reference, Complaint.id != complaint.id).first()
        if cited and (not complaint.customer_id or cited.customer_id == complaint.customer_id):
            related[cited.id] = cited

    if complaint.customer_id:
        text = normalize_text(complaint.normalized_text or f"{complaint.title} {complaint.description}").lower()
        others = (
            db.query(Complaint)
            .filter(
                Complaint.customer_id == complaint.customer_id,
                Complaint.id != complaint.id,
                Complaint.status.in_(OPEN_STATUSES),
            )
            .order_by(Complaint.id.desc())
            .limit(50)
            .all()
        )
        for other in others:
            same_order = bool(complaint.order_reference) and other.order_reference == complaint.order_reference
            similar = fuzz.token_set_ratio(text, (other.normalized_text or "").lower()) >= similarity_threshold
            if same_order or similar:
                related[other.id] = other

    codes = [row.complaint_code for row in sorted(related.values(), key=lambda r: r.id)]
    return {"is_repeat": bool(related), "open_count": len(related), "related_codes": codes}

from sqlalchemy.orm import Session

from complaint_processing.duplicates import OPEN_STATUSES
from database.models import Complaint, KnowledgeDocument
from security.audit import write_audit


def flag_complaints_on_policy_change(db: Session, document: KnowledgeDocument) -> list[str]:
    """Hidden policy-update challenge: find open complaints whose resolution relied on this policy.

    A complaint is affected when either pipeline cited the document code, or when the GenAI
    run was grounded on an older version of it. Those complaints are told to re-analyze so
    responses based on the obsolete version get revised.
    """
    affected: list[str] = []
    for complaint in db.query(Complaint).filter(Complaint.status.in_(OPEN_STATUSES)).all():
        genai = next((r for r in reversed(complaint.genai_runs) if r.structured_output), None)
        latest_val = complaint.validation_results[-1] if complaint.validation_results else None
        cited = {
            str((genai.structured_output if genai else {}).get("policy_id") or ""),
            str((latest_val.python_output if latest_val else {}).get("policy_id") or ""),
        }
        grounded_versions = {
            p.get("version") for p in (genai.policy_versions if genai else []) if p.get("document_code") == document.document_code
        }
        stale_grounding = bool(grounded_versions) and document.version not in grounded_versions
        if document.document_code in cited or stale_grounding:
            # Staff-facing notice (History tab); the customer's latest update is left alone.
            write_audit(
                db,
                actor_id=None,
                entity_type="complaint",
                entity_id=complaint.complaint_code,
                action="policy_changed",
                details={"policy": document.document_code, "version": document.version, "note": "Re-analysis recommended"},
            )
            affected.append(complaint.complaint_code)
    return affected

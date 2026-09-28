from datetime import date

from sqlalchemy.orm import Session

from database.models import DocumentCategory, DocumentStatus, KnowledgeDocument

PRECEDENCE = {
    DocumentCategory.policy: 10,
    DocumentCategory.compliance: 15,
    DocumentCategory.sla: 20,
    DocumentCategory.sop: 30,
    DocumentCategory.escalation: 35,
    DocumentCategory.routing: 40,
    DocumentCategory.guideline: 50,
    DocumentCategory.template: 60,
    DocumentCategory.faq: 80,
}


def is_usable_policy(document: KnowledgeDocument, as_of: date | None = None) -> bool:
    as_of = as_of or date.today()
    if document.status != DocumentStatus.active:
        return False
    if document.effective_date and document.effective_date > as_of:
        return False
    if document.expiry_date and document.expiry_date < as_of:
        return False
    return True


def policy_status(db: Session, document_code: str | None) -> dict:
    """Applicability of a cited policy code (SRS step 26) based on its versions in the KB."""
    code = (document_code or "").strip()
    if not code:
        return {"known": False, "applicability": "not_applicable", "active_version": None, "versions": []}
    docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.document_code == code).all()
    versions = [{"version": d.version, "status": d.status.value} for d in docs]
    if not docs:
        return {"known": False, "applicability": "not_applicable", "active_version": None, "versions": []}
    usable = sorted((d for d in docs if is_usable_policy(d)), key=sort_key)
    if usable:
        return {"known": True, "applicability": "applicable", "active_version": usable[0].version, "versions": versions}
    return {"known": True, "applicability": "outdated", "active_version": None, "versions": versions}


def sort_key(document: KnowledgeDocument) -> tuple:
    return (
        PRECEDENCE.get(document.category, 90),
        -(document.effective_date.toordinal() if document.effective_date else 0),
        document.version,
    )

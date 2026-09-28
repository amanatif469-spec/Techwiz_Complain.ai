import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session, selectinload

from config.settings import get_settings
from database.models import (
    DocumentCategory,
    DocumentChunk,
    DocumentStatus,
    KnowledgeDocument,
)
from database.session import get_db
from document_processing.chunking import chunk_sections
from document_processing.parser import parse_document
from document_processing.validate import file_checksum, safe_filename, validate_knowledge_file
from knowledge_base.impact import flag_complaints_on_policy_change
from knowledge_base.precedence import PRECEDENCE, is_usable_policy
from security.audit import write_audit
from security.auth import AdminUser, StaffUser
from security.prompt_injection import detect_prompt_injection

router = APIRouter(prefix="/api/v1/knowledge-base", tags=["knowledge-base"])

DOCUMENT_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,40}$")
VERSION = re.compile(r"^\d+(\.\d+){0,3}$")


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be an ISO date (YYYY-MM-DD).") from exc


def _enum(enum_cls, value: str, field: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(e.value for e in enum_cls)
        raise HTTPException(status_code=422, detail=f"Invalid {field} '{value}'. Allowed: {allowed}.") from exc


@router.post("/documents")
def upload_document(
    user: AdminUser,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    document_code: str = Form(...),
    title: str = Form(...),
    version: str = Form(...),
    category: str = Form(...),
    status: str = Form("active"),
    effective_date: str | None = Form(None),
    expiry_date: str | None = Form(None),
):
    document_code = document_code.strip().upper()
    version = version.strip().lstrip("vV")
    if not DOCUMENT_CODE.match(document_code):
        raise HTTPException(status_code=422, detail="Document ID must be 3-41 characters of A-Z, 0-9 and '-' (e.g. REF-POL-02).")
    if not VERSION.match(version):
        raise HTTPException(status_code=422, detail="Version must look like 1, 1.0 or 2.1.3.")
    if not title.strip():
        raise HTTPException(status_code=422, detail="Title is required.")
    cat = _enum(DocumentCategory, category, "category")
    doc_status = _enum(DocumentStatus, status, "status")
    effective = _parse_date(effective_date, "effective_date") or date.today()
    expiry = _parse_date(expiry_date, "expiry_date")
    if expiry and expiry <= effective:
        raise HTTPException(status_code=422, detail="Expiry date must be after the effective date.")

    content = file.file.read()
    validate_knowledge_file(file, content)
    checksum = file_checksum(content)
    duplicate = db.query(KnowledgeDocument).filter(KnowledgeDocument.checksum == checksum).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Duplicate document of {duplicate.document_code} v{duplicate.version}")
    existing_version = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.document_code == document_code, KnowledgeDocument.version == version)
        .first()
    )
    if existing_version:
        raise HTTPException(status_code=409, detail="Document ID + version already exists.")
    dest_dir = get_settings().upload_path / "knowledge"
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(file.filename or "", f"{document_code}.txt")
    dest = dest_dir / f"{document_code}_{version}_{filename}"
    dest.write_bytes(content)

    sections = parse_document(dest, content)
    chunks = chunk_sections(sections)
    if not chunks:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Could not extract text from the document.")

    doc = KnowledgeDocument(
        document_code=document_code,
        title=title.strip(),
        version=version,
        category=cat,
        status=doc_status,
        precedence_rank=PRECEDENCE.get(cat, 50),
        effective_date=effective,
        expiry_date=expiry,
        checksum=checksum,
        original_filename=filename,
        storage_path=str(dest),
        uploaded_by_id=user.id,
        content_text="\n\n".join(section["content"] for section in sections),
    )
    db.add(doc)
    db.flush()
    superseded = _supersede_previous(db, doc) if doc.status == DocumentStatus.active else []
    for chunk in chunks:
        db.add(
            DocumentChunk(
                chunk_code=f"{document_code}-v{version}-C{chunk['ordinal']:03d}",
                document_id=doc.id,
                section=chunk["section"],
                heading=chunk["heading"],
                page_number=chunk["page_number"],
                version=version,
                content=chunk["content"],
            )
        )
    warnings = []
    injection = detect_prompt_injection(doc.content_text)
    if injection["detected"]:
        warnings.append("Document contains instruction-like text; it is passed to GenAI only as reference data.")
    if not is_usable_policy(doc) and doc.status == DocumentStatus.active:
        warnings.append("Document is active but outside its effective/expiry window, so retrieval will not treat it as usable.")
    affected = flag_complaints_on_policy_change(db, doc) if doc.status == DocumentStatus.active else []
    write_audit(
        db,
        actor_id=user.id,
        entity_type="document",
        entity_id=document_code,
        action="upload",
        details={
            "version": version,
            "chunks": len(chunks),
            "superseded_versions": superseded,
            "affected_open_complaints": affected,
            "injection_patterns": injection["patterns"],
        },
    )
    db.commit()
    db.refresh(doc)
    return {
        "id": doc.id,
        "document_code": doc.document_code,
        "version": doc.version,
        "chunks": len(chunks),
        "superseded_versions": superseded,
        "affected_open_complaints": len(affected),
        "affected_complaint_codes": affected,
        "warnings": warnings,
    }


@router.get("/documents")
def list_documents(user: StaffUser, db: Session = Depends(get_db)):
    rows = db.query(KnowledgeDocument).options(selectinload(KnowledgeDocument.chunks)).order_by(KnowledgeDocument.id.desc()).all()
    return [
        {
            "id": row.id,
            "document_code": row.document_code,
            "title": row.title,
            "version": row.version,
            "category": row.category.value,
            "status": row.status.value,
            "usable": is_usable_policy(row),
            "precedence_rank": row.precedence_rank,
            "effective_date": row.effective_date,
            "expiry_date": row.expiry_date,
            "chunk_count": len(row.chunks),
            "original_filename": row.original_filename,
        }
        for row in rows
    ]


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: int, user: StaffUser, db: Session = Depends(get_db)):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.id).all()
    return [
        {
            "chunk_code": c.chunk_code,
            "section": c.section,
            "heading": c.heading,
            "page_number": c.page_number,
            "version": c.version,
            "content": c.content,
        }
        for c in chunks
    ]


@router.patch("/documents/{document_id}/status")
def update_status(document_id: int, status: str, user: AdminUser, db: Session = Depends(get_db)):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    previous = doc.status.value
    doc.status = _enum(DocumentStatus, status, "status")
    superseded: list[str] = []
    affected: list[str] = []
    if doc.status == DocumentStatus.active:
        superseded = _supersede_previous(db, doc)
        affected = flag_complaints_on_policy_change(db, doc)
    write_audit(
        db,
        actor_id=user.id,
        entity_type="document",
        entity_id=doc.document_code,
        action="status",
        details={"version": doc.version, "from": previous, "to": doc.status.value, "affected_open_complaints": affected},
    )
    db.commit()
    return {"id": doc.id, "status": doc.status.value, "superseded_versions": superseded, "affected_complaint_codes": affected}


def _supersede_previous(db: Session, current: KnowledgeDocument) -> list[str]:
    previous_rows = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.document_code == current.document_code,
            KnowledgeDocument.id != current.id,
            KnowledgeDocument.status == DocumentStatus.active,
        )
        .all()
    )
    for row in previous_rows:
        row.status = DocumentStatus.previous
        row.superseded_by_id = current.id
    return [row.version for row in previous_rows]

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from config.settings import get_settings

ALLOWED_KB_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
}
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}
ALLOWED_COMPLAINT_ATTACHMENTS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt"}


def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def validate_knowledge_file(upload: UploadFile, content: bytes) -> None:
    settings = get_settings()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty files are not allowed.")
    ext = _extension(upload.filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File exceeds {settings.max_upload_mb} MB limit.")
    _check_signature(ext, content)


def validate_complaint_attachment(upload: UploadFile, content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=400, detail="Empty attachment is not allowed.")
    ext = _extension(upload.filename or "")
    if ext not in ALLOWED_COMPLAINT_ATTACHMENTS:
        raise HTTPException(status_code=400, detail=f"Unsupported attachment type: {ext}")
    max_mb = get_settings().max_upload_mb
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Attachment exceeds {max_mb} MB limit.")
    _check_signature(ext, content)


# A renamed executable must not pass as a PDF or DOCX just because of its extension.
SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def _check_signature(ext: str, content: bytes) -> None:
    expected = SIGNATURES.get(ext)
    if expected and not content.startswith(expected):
        raise HTTPException(status_code=400, detail=f"File content does not match its {ext} extension.")


def safe_filename(name: str, default: str = "upload.bin") -> str:
    """Drop any directory part and unusual characters so uploads cannot escape their folder."""
    base = Path((name or "").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned[:120] or default


def file_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

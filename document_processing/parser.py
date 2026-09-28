from __future__ import annotations

from pathlib import Path


def parse_document(path: Path, content: bytes | None = None) -> list[dict]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        return _parse_docx(path)
    text = (content.decode("utf-8", errors="ignore") if content is not None else path.read_text(encoding="utf-8", errors="ignore"))
    heading = path.stem
    return [{"heading": heading, "section": heading, "page_number": 1, "content": text}]


def _parse_pdf(path: Path) -> list[dict]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return [{"heading": path.stem, "section": path.stem, "page_number": 1, "content": ""}]
    pages = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append(
                    {
                        "heading": f"Page {index}",
                        "section": f"{index}",
                        "page_number": index,
                        "content": text,
                    }
                )
    return pages


def _parse_docx(path: Path) -> list[dict]:
    from docx import Document

    document = Document(path)
    blocks: list[dict] = []
    current_heading = path.stem
    buffer: list[str] = []
    section_no = 1
    for para in document.paragraphs:
        style = (para.style.name or "").lower() if para.style else ""
        text = para.text.strip()
        if not text:
            continue
        if "heading" in style:
            if buffer:
                blocks.append(
                    {
                        "heading": current_heading,
                        "section": str(section_no),
                        "page_number": None,
                        "content": "\n".join(buffer),
                    }
                )
                section_no += 1
                buffer = []
            current_heading = text
        else:
            buffer.append(text)
    if buffer:
        blocks.append(
            {
                "heading": current_heading,
                "section": str(section_no),
                "page_number": None,
                "content": "\n".join(buffer),
            }
        )
    return blocks

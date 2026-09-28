from __future__ import annotations


def chunk_sections(sections: list[dict], *, max_chars: int = 1200) -> list[dict]:
    chunks: list[dict] = []
    counter = 1
    for section in sections:
        text = (section.get("content") or "").strip()
        if not text:
            continue
        pieces = _split(text, max_chars)
        for piece in pieces:
            chunks.append(
                {
                    "heading": section.get("heading") or "",
                    "section": section.get("section") or "",
                    "page_number": section.get("page_number"),
                    "content": piece,
                    "ordinal": counter,
                }
            )
            counter += 1
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    out: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= max_chars:
            current = f"{current}\n{para}".strip()
        else:
            if current:
                out.append(current)
            if len(para) <= max_chars:
                current = para
            else:
                for i in range(0, len(para), max_chars):
                    out.append(para[i : i + max_chars])
                current = ""
    if current:
        out.append(current)
    return out

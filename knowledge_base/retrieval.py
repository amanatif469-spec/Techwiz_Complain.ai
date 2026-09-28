from __future__ import annotations

import math
import re
from collections import Counter

from sqlalchemy.orm import Session, joinedload

from database.models import DocumentChunk, DocumentStatus, KnowledgeDocument
from knowledge_base.precedence import is_usable_policy, sort_key

TOKEN = re.compile(r"[a-zA-Z]{3,}")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text or "")]


def retrieve_policy_chunks(db: Session, query: str, limit: int = 6) -> list[dict]:
    query_tokens = Counter(_tokens(query))
    if not query_tokens:
        return []
    chunks = (
        db.query(DocumentChunk)
        .join(KnowledgeDocument)
        .options(joinedload(DocumentChunk.document))
        .filter(KnowledgeDocument.status.in_([DocumentStatus.active, DocumentStatus.previous]))
        .all()
    )
    scored: list[tuple[float, DocumentChunk]] = []
    for chunk in chunks:
        doc = chunk.document
        usable = is_usable_policy(doc)
        tokens = Counter(_tokens(chunk.content + " " + chunk.heading))
        overlap = sum((query_tokens & tokens).values())
        if overlap <= 0:
            continue
        precedence_boost = 1.0 if usable else 0.25
        length_norm = math.log(2 + len(tokens))
        score = (overlap / length_norm) * precedence_boost
        scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], sort_key(item[1].document)))
    results = []
    for score, chunk in scored[:limit]:
        doc = chunk.document
        results.append(
            {
                "chunk_code": chunk.chunk_code,
                "document_code": doc.document_code,
                "title": doc.title,
                "version": doc.version,
                "status": doc.status.value,
                "section": chunk.section,
                "heading": chunk.heading,
                "page_number": chunk.page_number,
                "content": chunk.content[:1500],
                "score": round(score, 4),
                "usable": is_usable_policy(doc),
            }
        )
    return results

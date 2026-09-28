"""Whole-word keyword matching shared by the rule, routing, and escalation engines.

Plain substring matching produced false escalations: "issue" contains "sue" (legal
threat), "immediately" contains "media", "courteous" contains "court". Keywords must
match on word boundaries, with a few inflection suffixes so "overheat" still matches
"overheating" and "shock" matches "shocked".
"""

from __future__ import annotations

import re
from functools import lru_cache

_SUFFIX = r"(?:s|es|ed|d|ing)?"


@lru_cache(maxsize=4096)
def _pattern(keyword: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in keyword.lower().split()]
    body = r"\s+".join(words)
    return re.compile(rf"(?<![a-z0-9]){body}{_SUFFIX}(?![a-z0-9])")


def keyword_hits(text: str, keywords) -> list[str]:
    """Return the keywords that occur in ``text`` as whole words (case-insensitive)."""
    lowered = (text or "").lower()
    hits: list[str] = []
    for keyword in keywords or []:
        kw = str(keyword or "").strip().lower()
        if kw and kw not in hits and _pattern(kw).search(lowered):
            hits.append(kw)
    return hits


def first_position(text: str, keywords) -> int:
    """Character offset of the earliest keyword hit, or a large number when none match."""
    lowered = (text or "").lower()
    positions = [m.start() for kw in keywords or [] if str(kw or "").strip() and (m := _pattern(str(kw).strip().lower()).search(lowered))]
    return min(positions) if positions else 10**9


def keyword_score(text: str, keywords) -> int:
    """Multi-word phrases are more specific than single words, so they weigh more."""
    return sum(len(hit.split()) for hit in keyword_hits(text, keywords))

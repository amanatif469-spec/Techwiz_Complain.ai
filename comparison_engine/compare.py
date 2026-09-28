COMPARISON_FIELDS = [
    "issue_category",
    "subcategory",
    "department",
    "urgency",
    "priority",
    "escalation_required",
    "policy_id",
]


def compare_outputs(genai: dict, python_out: dict) -> dict:
    comparisons = {}
    matches = 0
    mismatches = 0
    explanations = []
    for field in COMPARISON_FIELDS:
        left = _norm(genai.get(field))
        right = _norm(python_out.get(field))
        equal = left == right
        comparisons[field] = {"genai": genai.get(field), "python": python_out.get(field), "match": equal}
        if equal:
            matches += 1
        else:
            mismatches += 1
            explanations.append(f"{field}: GenAI={genai.get(field)!r} Python={python_out.get(field)!r}")
    total = max(matches + mismatches, 1)
    score = round(100 * matches / total, 2)
    routing_or_escalation_mismatch = (
        comparisons.get("department", {}).get("match") is False
        or comparisons.get("issue_category", {}).get("match") is False
        or comparisons.get("escalation_required", {}).get("match") is False
    )
    significant = routing_or_escalation_mismatch or mismatches >= 3
    status = "verified" if mismatches == 0 else ("manual_review" if significant else "partial_match")
    return {
        "field_comparisons": comparisons,
        "match_count": matches,
        "mismatch_count": mismatches,
        "verification_score": score,
        "verification_status": status,
        "explanation": "; ".join(explanations),
        "requires_manual_review": status == "manual_review",
    }


def _norm(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower()

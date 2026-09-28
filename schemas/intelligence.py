from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntelligenceOutput(BaseModel):
    """Canonical structured output for Pipeline 1 (GenAI)."""

    model_config = ConfigDict(extra="allow")

    complaint_id: str
    primary_issue: str
    secondary_issues: list[str] = Field(default_factory=list)
    issue_category: str
    subcategory: str
    sentiment: str
    emotion_indicators: list[str] = Field(default_factory=list)
    urgency: str
    priority: str
    department: str
    supporting_departments: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    policy_id: str = ""
    policy_section: str = ""
    policy_applicability: str = "applicable"
    resolution_steps: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    escalation_level: str = "no_escalation"
    escalation_reason: str = ""
    escalation_notes: str = ""
    response_type: str = ""
    customer_response: str = ""
    follow_up_required: bool = True
    follow_up_communication: str = ""
    clarification_questions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    complaint_summary: str = ""
    agent_guidance: list[str] = Field(default_factory=list)
    refund_eligible: bool | None = None
    replacement_eligible: bool | None = None
    compensation_recommended: bool = False


ALLOWED_SENTIMENT = {"positive", "neutral", "negative", "strongly_negative"}
ALLOWED_URGENCY = {"low", "medium", "high", "critical"}
ALLOWED_PRIORITY = {"P0", "P1", "P2", "P3"}
ALLOWED_ESCALATION = {
    "no_escalation",
    "supervisor_review",
    "department_manager",
    "specialist_team",
    "compliance_review",
    "critical_management",
}
ALLOWED_APPLICABILITY = {"applicable", "conditionally_applicable", "not_applicable", "outdated"}

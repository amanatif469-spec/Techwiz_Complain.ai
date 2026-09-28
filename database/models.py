from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB

JSONB = JSON().with_variant(_PG_JSONB, "postgresql")
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.session import Base


def pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    # VARCHAR-backed so the same logical enum can appear on many tables.
    return Enum(enum_cls, name=name, native_enum=False, length=64)


class UserRole(str, enum.Enum):
    customer = "customer"
    agent = "agent"
    reviewer = "reviewer"
    manager = "manager"
    administrator = "administrator"


class ComplaintStatus(str, enum.Enum):
    new = "new"
    analyzed = "analyzed"
    assigned = "assigned"
    in_progress = "in_progress"
    awaiting_customer = "awaiting_customer"
    escalated = "escalated"
    resolved = "resolved"
    closed = "closed"
    reopened = "reopened"


class DocumentStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    previous = "previous"
    superseded = "superseded"


class DocumentCategory(str, enum.Enum):
    policy = "policy"
    sop = "sop"
    faq = "faq"
    sla = "sla"
    routing = "routing"
    escalation = "escalation"
    template = "template"
    compliance = "compliance"
    guideline = "guideline"


class UrgencyLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class PriorityCode(str, enum.Enum):
    P3 = "P3"
    P2 = "P2"
    P1 = "P1"
    P0 = "P0"


class SentimentLabel(str, enum.Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    strongly_negative = "strongly_negative"


class EscalationLevel(str, enum.Enum):
    no_escalation = "no_escalation"
    supervisor_review = "supervisor_review"
    department_manager = "department_manager"
    specialist_team = "specialist_team"
    compliance_review = "compliance_review"
    critical_management = "critical_management"


class PolicyApplicability(str, enum.Enum):
    applicable = "applicable"
    conditionally_applicable = "conditionally_applicable"
    not_applicable = "not_applicable"
    outdated = "outdated"


class CustomerType(str, enum.Enum):
    standard = "standard"
    vip = "vip"
    wholesale = "wholesale"
    enterprise = "enterprise"


class Channel(str, enum.Enum):
    web = "web"
    email = "email"
    chat = "chat"
    portal = "portal"
    messaging = "messaging"


class ReviewActionType(str, enum.Enum):
    approve = "approve"
    reject = "reject"
    modify = "modify"
    reclassify = "reclassify"
    reassign = "reassign"
    escalate = "escalate"
    regenerate = "regenerate"
    comment = "comment"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), default=UserRole.customer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), nullable=True)

    department: Mapped[Optional["Department"]] = relationship(back_populates="users")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="user", uselist=False)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    customer_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    customer_type: Mapped[CustomerType] = mapped_column(
        pg_enum(CustomerType, "customer_type"), default=CustomerType.standard
    )
    email: Mapped[str] = mapped_column(String(255), index=True)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[Optional[User]] = relationship(back_populates="customer")
    complaints: Mapped[list["Complaint"]] = relationship(back_populates="customer")


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    users: Mapped[list[User]] = relationship(back_populates="department")
    categories: Mapped[list["ComplaintCategory"]] = relationship(back_populates="default_department")


class ComplaintCategory(Base, TimestampMixin):
    __tablename__ = "complaint_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    default_department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    default_department: Mapped[Optional[Department]] = relationship(back_populates="categories")
    subcategories: Mapped[list["ComplaintSubcategory"]] = relationship(back_populates="category")


class ComplaintSubcategory(Base, TimestampMixin):
    __tablename__ = "complaint_subcategories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("complaint_categories.id"))
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    keywords: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped[ComplaintCategory] = relationship(back_populates="subcategories")
    __table_args__ = (UniqueConstraint("category_id", "code", name="uq_subcategory_code"),)


class PriorityRule(Base, TimestampMixin):
    __tablename__ = "priority_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    urgency: Mapped[UrgencyLevel] = mapped_column(pg_enum(UrgencyLevel, "urgency_level"))
    priority: Mapped[PriorityCode] = mapped_column(pg_enum(PriorityCode, "priority_code"))
    response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_hours: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")


class SlaPolicy(Base, TimestampMixin):
    __tablename__ = "sla_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    customer_type: Mapped[Optional[CustomerType]] = mapped_column(
        pg_enum(CustomerType, "customer_type"), nullable=True
    )
    priority: Mapped[PriorityCode] = mapped_column(pg_enum(PriorityCode, "priority_code"))
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_hours: Mapped[int] = mapped_column(Integer)
    risk_threshold_percent: Mapped[int] = mapped_column(Integer, default=75)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_code: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(32))
    category: Mapped[DocumentCategory] = mapped_column(pg_enum(DocumentCategory, "document_category"))
    status: Mapped[DocumentStatus] = mapped_column(
        pg_enum(DocumentStatus, "document_status"), default=DocumentStatus.draft
    )
    precedence_rank: Mapped[int] = mapped_column(Integer, default=50)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512))
    content_text: Mapped[str] = mapped_column(Text, default="")
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    superseded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=True)

    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")
    __table_args__ = (UniqueConstraint("document_code", "version", name="uq_document_version"),)


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"))
    section: Mapped[str] = mapped_column(String(128), default="")
    heading: Mapped[str] = mapped_column(String(255), default="")
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    version: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class ResolutionRule(Base, TimestampMixin):
    __tablename__ = "resolution_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category_code: Mapped[str] = mapped_column(String(64), index=True)
    subcategory_code: Mapped[str] = mapped_column(String(64), index=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    department_code: Mapped[str] = mapped_column(String(32))
    supporting_department_codes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    urgency: Mapped[UrgencyLevel] = mapped_column(pg_enum(UrgencyLevel, "urgency_level"))
    priority: Mapped[PriorityCode] = mapped_column(pg_enum(PriorityCode, "priority_code"))
    policy_code: Mapped[str] = mapped_column(String(64), default="")
    policy_section: Mapped[str] = mapped_column(String(64), default="")
    escalation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_level: Mapped[EscalationLevel] = mapped_column(
        pg_enum(EscalationLevel, "escalation_level"), default=EscalationLevel.no_escalation
    )
    required_actions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    prohibited_actions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=True)
    refund_eligible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    replacement_eligible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    compensation_permitted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class EscalationRule(Base, TimestampMixin):
    __tablename__ = "escalation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    keywords: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    categories: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    min_repeat_count: Mapped[int] = mapped_column(Integer, default=0)
    customer_types: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    escalation_level: Mapped[EscalationLevel] = mapped_column(pg_enum(EscalationLevel, "escalation_level"))
    reason: Mapped[str] = mapped_column(Text)
    force_urgency: Mapped[Optional[UrgencyLevel]] = mapped_column(
        pg_enum(UrgencyLevel, "urgency_level"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PromptTemplate(Base, TimestampMixin):
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(255), default="")
    system_prompt: Mapped[str] = mapped_column(Text)
    user_template: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_version"),)


class Complaint(Base, TimestampMixin):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"))
    submitted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    product_or_service: Mapped[str] = mapped_column(String(255), default="")
    order_reference: Mapped[str] = mapped_column(String(128), default="")
    previous_complaint_reference: Mapped[str] = mapped_column(String(32), default="")
    customer_type: Mapped[CustomerType] = mapped_column(
        pg_enum(CustomerType, "customer_type"), default=CustomerType.standard
    )
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "channel"), default=Channel.web)
    preferred_contact_channel: Mapped[str] = mapped_column(String(64), default="email")
    requested_resolution: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ComplaintStatus] = mapped_column(
        pg_enum(ComplaintStatus, "complaint_status"), default=ComplaintStatus.new
    )
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("complaints.id"), nullable=True)
    is_repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    is_adversarial: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_first_response_due: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_resolution_due: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_update: Mapped[str] = mapped_column(Text, default="Submitted")

    customer: Mapped[Optional[Customer]] = relationship(back_populates="complaints")
    assigned_department: Mapped[Optional[Department]] = relationship(foreign_keys=[assigned_department_id])
    assigned_to: Mapped[Optional[User]] = relationship(foreign_keys=[assigned_to_id])
    duplicate_of: Mapped[Optional["Complaint"]] = relationship(remote_side=[id], foreign_keys=[duplicate_of_id])
    attachments: Mapped[list["ComplaintAttachment"]] = relationship(back_populates="complaint")
    # Ordered so that [-1] is always the newest record for dashboards and review.
    genai_runs: Mapped[list["GenAIRun"]] = relationship(
        back_populates="complaint", order_by="GenAIRun.id"
    )
    validation_results: Mapped[list["ValidationResult"]] = relationship(
        back_populates="complaint", order_by="ValidationResult.id"
    )
    comparisons: Mapped[list["ComparisonResult"]] = relationship(
        back_populates="complaint", order_by="ComparisonResult.id"
    )
    reviews: Mapped[list["ReviewAction"]] = relationship(back_populates="complaint", order_by="ReviewAction.id")
    followups: Mapped[list["FollowUp"]] = relationship(back_populates="complaint")


class ComplaintAttachment(Base, TimestampMixin):
    __tablename__ = "complaint_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    storage_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer)

    complaint: Mapped[Complaint] = relationship(back_populates="attachments")


class GenAIRun(Base, TimestampMixin):
    __tablename__ = "genai_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_name: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    policy_versions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    raw_response: Mapped[str] = mapped_column(Text, default="")
    structured_output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_valid_schema: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str] = mapped_column(Text, default="")
    routed_to_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)

    complaint: Mapped[Complaint] = relationship(back_populates="genai_runs")


class ValidationResult(Base, TimestampMixin):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    genai_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("genai_runs.id"), nullable=True)
    python_output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    flags: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    verification_score: Mapped[float] = mapped_column(Float, default=0)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)

    complaint: Mapped[Complaint] = relationship(back_populates="validation_results")


class ComparisonResult(Base, TimestampMixin):
    __tablename__ = "comparisons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    field_comparisons: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0)
    verification_status: Mapped[str] = mapped_column(String(32), default="pending")
    explanation: Mapped[str] = mapped_column(Text, default="")

    complaint: Mapped[Complaint] = relationship(back_populates="comparisons")


class ReviewAction(Base, TimestampMixin):
    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[ReviewActionType] = mapped_column(pg_enum(ReviewActionType, "review_action_type"))
    original_recommendation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    final_decision: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    comments: Mapped[str] = mapped_column(Text, default="")

    complaint: Mapped[Complaint] = relationship(back_populates="reviews")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class FollowUp(Base, TimestampMixin):
    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text)
    follow_up_type: Mapped[str] = mapped_column(String(64), default="status_update")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    complaint: Mapped[Complaint] = relationship(back_populates="followups")

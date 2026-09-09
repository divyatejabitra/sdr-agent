from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sdr_workspace.db import Base
from sdr_workspace.enums import (
    AudienceSegment,
    CampaignStatus,
    CampaignType,
    ConsentStatus,
    ConsentTopic,
    DeliveryMode,
    EnrollmentStatus,
    JobStatus,
    JobType,
    KnowledgeStatus,
    LifecycleStatus,
    MessageStatus,
    OfferStatus,
    ProviderName,
    ReplyIntent,
    SalesTaskStatus,
    SuppressionScope,
)


def _utcnow():
    return func.timezone("utc", func.now())


class OwnerUser(Base):
    __tablename__ = "owner_users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    original_email: Mapped[str] = mapped_column(String(320))
    email_key: Mapped[str] = mapped_column(String(320), unique=True)
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    segment: Mapped[str] = mapped_column(String(40), default=AudienceSegment.PROSPECT.value)
    country: Mapped[str] = mapped_column(String(80), default="US")
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    source: Mapped[str] = mapped_column(String(80), default="import")
    lifecycle_status: Mapped[str] = mapped_column(String(40), default=LifecycleStatus.NEW.value)
    last_purchase_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_marketing_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audience_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class ConsentEvent(Base):
    __tablename__ = "consent_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    contact_id: Mapped[UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    channel: Mapped[str] = mapped_column(String(40), default="email")
    topic: Mapped[str] = mapped_column(String(40), default=ConsentTopic.MARKETING.value)
    status: Mapped[str] = mapped_column(String(20), default=ConsentStatus.GRANTED.value)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(80))
    evidence_ref: Mapped[str] = mapped_column(String(200), default="")
    policy_version: Mapped[str] = mapped_column(String(40), default="v1")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    evidence_id: Mapped[str] = mapped_column(String(80), unique=True)
    source_ref: Mapped[str] = mapped_column(String(300), default="internal")
    status: Mapped[str] = mapped_column(String(20), default=KnowledgeStatus.DRAFT.value)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    product: Mapped[str] = mapped_column(String(200))
    price_text: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    terms: Mapped[str] = mapped_column(Text)
    eligibility: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str] = mapped_column(String(40), default="")
    destination_url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=OfferStatus.DRAFT.value)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    campaign_type: Mapped[str] = mapped_column(String(40))
    audience_segment: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default=CampaignStatus.DRAFT.value)
    content_revision: Mapped[int] = mapped_column(Integer, default=1)
    approved_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_mode: Mapped[str] = mapped_column(String(12), default=DeliveryMode.MOCK.value)
    sending_window_start: Mapped[str] = mapped_column(String(8), default="09:00")
    sending_window_end: Mapped[str] = mapped_column(String(8), default="17:00")
    min_gap_days: Mapped[int] = mapped_column(Integer, default=0)
    active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offer_id: Mapped[UUID | None] = mapped_column(ForeignKey("offers.id"), nullable=True)
    external_campaign_id: Mapped[str] = mapped_column(String(120), default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())

    steps: Mapped[list[CampaignStep]] = relationship(back_populates="campaign", order_by="CampaignStep.step_number")


class CampaignStep(Base):
    __tablename__ = "campaign_steps"
    __table_args__ = (UniqueConstraint("campaign_id", "step_number"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"))
    step_number: Mapped[int] = mapped_column(Integer)
    delay_days: Mapped[int] = mapped_column(Integer, default=0)
    template: Mapped[str] = mapped_column(Text)
    call_to_action: Mapped[str] = mapped_column(String(300), default="")
    offer_id: Mapped[UUID | None] = mapped_column(ForeignKey("offers.id"), nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="steps")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        Index(
            "uq_enrollments_one_active",
            "contact_id",
            unique=True,
            postgresql_where=text(
                "status IN ('active', 'paused', 'unknown', 'pending_cancellation')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    contact_id: Mapped[UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    delivery_mode: Mapped[str] = mapped_column(String(12), default=DeliveryMode.MOCK.value)
    status: Mapped[str] = mapped_column(String(40), default=EnrollmentStatus.ACTIVE.value)
    current_step: Mapped[int] = mapped_column(Integer, default=1)
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[str] = mapped_column(String(80), default="")
    external_lead_id: Mapped[str] = mapped_column(String(120), default="")
    provider_sync_status: Mapped[str] = mapped_column(String(40), default="local")
    requested_resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("enrollment_id", "step_number"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("enrollments.id"), index=True)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"))
    contact_id: Mapped[UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    step_number: Mapped[int] = mapped_column(Integer)
    delivery_mode: Mapped[str] = mapped_column(String(12))
    draft_subject: Mapped[str] = mapped_column(String(300), default="")
    draft_body: Mapped[str] = mapped_column(Text, default="")
    final_subject: Mapped[str] = mapped_column(String(300), default="")
    final_body: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(40), default="draft-v1")
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(40), default=MessageStatus.DRAFTED.value)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    provider: Mapped[str] = mapped_column(String(40), default="")
    provider_message_id: Mapped[str] = mapped_column(String(200), default="")
    correlation_id: Mapped[str] = mapped_column(String(80), default="")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    __table_args__ = (UniqueConstraint("provider", "external_event_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[str] = mapped_column(String(20), default="pending")
    delivery_mode: Mapped[str] = mapped_column(String(12), default=DeliveryMode.MOCK.value)


class Suppression(Base):
    __tablename__ = "suppressions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    contact_id: Mapped[UUID | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    email_key: Mapped[str] = mapped_column(String(320), index=True)
    scope: Mapped[str] = mapped_column(String(40), default=SuppressionScope.MARKETING.value)
    reason: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(80))
    provider_sync_status: Mapped[str] = mapped_column(String(40), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING.value)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str] = mapped_column(String(80), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    delivery_mode: Mapped[str] = mapped_column(String(12), default=DeliveryMode.MOCK.value)
    fence_token: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    correlation_id: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class SalesTask(Base):
    __tablename__ = "sales_tasks"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    contact_id: Mapped[UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    task_type: Mapped[str] = mapped_column(String(40), default="reply_follow_up")
    owner: Mapped[str] = mapped_column(String(120), default="owner")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=SalesTaskStatus.OPEN.value)
    summary: Mapped[str] = mapped_column(Text, default="")
    suggested_reply: Mapped[str] = mapped_column(Text, default="")
    intent: Mapped[str] = mapped_column(String(40), default=ReplyIntent.UNKNOWN.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class InboundReply(Base):
    __tablename__ = "inbound_replies"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    contact_id: Mapped[UUID | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    from_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300), default="")
    newest_content: Mapped[str] = mapped_column(Text)
    quoted_content: Mapped[str] = mapped_column(Text, default="")
    thread_id: Mapped[str] = mapped_column(String(200), default="")
    message_id_header: Mapped[str] = mapped_column(String(300), default="")
    intent: Mapped[str] = mapped_column(String(40), default="")
    paused_before_classify: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


class Conversion(Base):
    __tablename__ = "conversions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    contact_id: Mapped[UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_utcnow())


def content_hash(subject: str, body: str) -> str:
    return hashlib.sha256(f"{subject}\n{body}".encode()).hexdigest()


def json_dump(data: Any) -> str:
    return json.dumps(data, default=str)

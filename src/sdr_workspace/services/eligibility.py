from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock
from sdr_workspace.config import Settings
from sdr_workspace.enums import (
    CampaignStatus,
    CampaignType,
    DeliveryMode,
    EnrollmentStatus,
    KnowledgeStatus,
    MessageStatus,
    OfferStatus,
)
from sdr_workspace.models import Campaign, CampaignStep, Contact, Enrollment, KnowledgeSource, Message, Offer
from sdr_workspace.services.permission import has_marketing_consent, is_suppressed
from sdr_workspace.services.scheduling import in_sending_window, next_in_window


@dataclass
class EligibilityResult:
    allowed: bool
    reason_codes: list[str] = field(default_factory=list)
    next_eligible_at: datetime | None = None


def evaluate_eligibility(
    session: Session,
    *,
    contact: Contact,
    campaign: Campaign,
    settings: Settings,
    clock: Clock,
    step: CampaignStep | None = None,
    for_live_send: bool = False,
) -> EligibilityResult:
    now = clock.now()
    reasons: list[str] = []
    next_at: datetime | None = None
    mode = DeliveryMode(campaign.delivery_mode)

    if campaign.status != CampaignStatus.ACTIVE.value:
        reasons.append("campaign_not_active")
    if campaign.approved_revision is None or campaign.approved_revision != campaign.content_revision:
        reasons.append("campaign_revision_unapproved")
    if campaign.active_from and now < campaign.active_from:
        reasons.append("campaign_not_started")
        next_at = campaign.active_from
    if campaign.active_until and now > campaign.active_until:
        reasons.append("campaign_ended")

    if campaign.audience_segment != contact.segment:
        reasons.append("audience_mismatch")

    offer = None
    offer_id = (step.offer_id if step and step.offer_id else campaign.offer_id)
    if offer_id:
        offer = session.get(Offer, offer_id)
        if offer is None:
            reasons.append("offer_missing")
        else:
            if offer.status != OfferStatus.APPROVED.value:
                reasons.append("offer_not_approved")
            if offer.status == OfferStatus.REDEEMED.value:
                reasons.append("offer_redeemed")
            if offer.starts_at and now < offer.starts_at:
                reasons.append("offer_not_started")
            if offer.expires_at and now > offer.expires_at:
                reasons.append("offer_expired")

    approved_knowledge = session.scalar(
        select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.APPROVED.value)
    )
    if not approved_knowledge:
        reasons.append("knowledge_unapproved")

    if campaign.campaign_type == CampaignType.COLD.value:
        if for_live_send and mode == DeliveryMode.LIVE and not contact.audience_reviewed:
            reasons.append("cold_audience_unreviewed")
        if not contact.country:
            reasons.append("country_unknown")
        if is_suppressed(session, contact, ["cold", "global"]):
            reasons.append("suppressed")
    else:
        if campaign.provider == "sendgrid" or campaign.campaign_type in {
            CampaignType.ONBOARDING.value,
            CampaignType.PROMOTIONAL.value,
            CampaignType.REENGAGEMENT.value,
        }:
            if not has_marketing_consent(session, contact, now):
                reasons.append("missing_marketing_consent")
        if is_suppressed(session, contact, ["marketing", "global"]):
            reasons.append("suppressed")

    active = session.scalars(
        select(Enrollment).where(
            Enrollment.contact_id == contact.id,
            Enrollment.status.in_(
                [
                    EnrollmentStatus.ACTIVE.value,
                    EnrollmentStatus.PAUSED.value,
                    EnrollmentStatus.UNKNOWN.value,
                    EnrollmentStatus.PENDING_CANCELLATION.value,
                ]
            ),
        )
    ).all()
    if len(active) >= settings.contact_active_enrollment_cap:
        # allowed if this campaign is the active one
        if not any(row.campaign_id == campaign.id for row in active):
            reasons.append("active_enrollment_exists")

    unresolved = session.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.contact_id == contact.id,
            Message.status == MessageStatus.UNKNOWN.value,
        )
    )
    if unresolved:
        reasons.append("unresolved_send")

    if not in_sending_window(now, contact.timezone, campaign.sending_window_start, campaign.sending_window_end):
        reasons.append("outside_sending_window")
        window_next = next_in_window(
            now, contact.timezone, campaign.sending_window_start, campaign.sending_window_end
        )
        next_at = max(filter(None, [next_at, window_next])) if next_at else window_next

    if campaign.campaign_type == CampaignType.PROMOTIONAL.value and contact.last_marketing_sent_at:
        gap = timedelta(days=max(campaign.min_gap_days, settings.promotional_min_gap_days))
        earliest = contact.last_marketing_sent_at + gap
        if now < earliest:
            reasons.append("frequency_cap")
            next_at = max(filter(None, [next_at, earliest])) if next_at else earliest

    if mode == DeliveryMode.LIVE and campaign.status != CampaignStatus.ACTIVE.value:
        reasons.append("live_requires_activation")
    if mode == DeliveryMode.TEST and contact.original_email.lower() not in settings.test_recipients:
        reasons.append("not_on_test_allowlist")
    if for_live_send and mode == DeliveryMode.LIVE and campaign.delivery_mode != DeliveryMode.LIVE.value:
        reasons.append("mode_mismatch")

    if not settings.business_postal_address or not settings.sender_email:
        reasons.append("missing_sender_identity")

    return EligibilityResult(allowed=not reasons, reason_codes=reasons, next_eligible_at=next_at)


def explain_contact(
    session: Session, contact_id: UUID, settings: Settings, clock: Clock, campaign: Campaign | None = None
) -> EligibilityResult:
    contact = session.get(Contact, contact_id)
    if contact is None:
        return EligibilityResult(False, ["contact_missing"])
    if campaign is None:
        reasons = []
        if is_suppressed(session, contact, ["global", "marketing", "cold"]):
            reasons.append("suppressed")
        return EligibilityResult(allowed=not reasons, reason_codes=reasons)
    return evaluate_eligibility(session, contact=contact, campaign=campaign, settings=settings, clock=clock)

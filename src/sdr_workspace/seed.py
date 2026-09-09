from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock, SystemClock
from sdr_workspace.config import Settings, get_settings
from sdr_workspace.enums import (
    AudienceSegment,
    CampaignStatus,
    CampaignType,
    ConsentStatus,
    DeliveryMode,
    KnowledgeStatus,
    OfferStatus,
)
from sdr_workspace.models import (
    AppSetting,
    Campaign,
    CampaignStep,
    ConsentEvent,
    Contact,
    KnowledgeSource,
    Offer,
    OwnerUser,
)
from sdr_workspace.services.contacts import import_contacts
from sdr_workspace.services.security import hash_password


def seed(session: Session, settings: Settings | None = None, clock: Clock | None = None) -> None:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    if session.scalar(select(OwnerUser).limit(1)):
        return
    session.add(
        OwnerUser(
            id=uuid4(),
            email=settings.owner_email.lower(),
            password_hash=hash_password(settings.owner_password),
        )
    )
    session.add(AppSetting(key="delivery_mode", value={"value": settings.delivery_mode}))
    session.add(AppSetting(key="app_name", value={"value": settings.app_name}))

    knowledge = [
        KnowledgeSource(
            id=uuid4(),
            title="Dispatch product",
            body="Harborline Dispatch gives mid-market carriers a live exception queue instead of spreadsheet fire drills.",
            evidence_id="kb-dispatch",
            source_ref="product/handbook",
            status=KnowledgeStatus.APPROVED.value,
        ),
        KnowledgeSource(
            id=uuid4(),
            title="Synthetic proof point",
            body="In Harborline's fictional demo, coordinators review exceptions in one queue. This is synthetic product copy, not a customer result.",
            evidence_id="kb-demo-claim",
            source_ref="demo",
            status=KnowledgeStatus.APPROVED.value,
        ),
    ]
    session.add_all(knowledge)
    offer = Offer(
        id=uuid4(),
        name="Two-week fictional pilot",
        product="Harborline Dispatch",
        price_text="Pilot at $0",
        currency="USD",
        terms="Synthetic demo offer. Not a real discount. Expires at the listed time.",
        eligibility="Opted-in demo customers in the sample dataset.",
        code="DEMO-PILOT",
        destination_url="https://harborline.example/demo-pilot",
        status=OfferStatus.APPROVED.value,
        starts_at=clock.now() - timedelta(days=1),
        expires_at=clock.now() + timedelta(days=60),
    )
    session.add(offer)
    session.flush()

    cold = Campaign(
        id=uuid4(),
        name="Prospect introduction",
        campaign_type=CampaignType.COLD.value,
        audience_segment=AudienceSegment.PROSPECT.value,
        provider="mock_sequencer",
        status=CampaignStatus.ACTIVE.value,
        content_revision=1,
        approved_revision=1,
        delivery_mode=DeliveryMode.MOCK.value,
        sending_window_start="09:00",
        sending_window_end="17:00",
        objective="Start a relevant sales conversation",
        external_campaign_id="mock-cold-1",
    )
    onboarding = Campaign(
        id=uuid4(),
        name="New user onboarding",
        campaign_type=CampaignType.ONBOARDING.value,
        audience_segment=AudienceSegment.OPTED_IN_USER.value,
        provider="mock_mail",
        status=CampaignStatus.ACTIVE.value,
        content_revision=1,
        approved_revision=1,
        delivery_mode=DeliveryMode.MOCK.value,
        sending_window_start="09:00",
        sending_window_end="17:00",
        offer_id=offer.id,
        objective="Introduce the product and encourage activation",
    )
    promo = Campaign(
        id=uuid4(),
        name="Customer upgrade",
        campaign_type=CampaignType.PROMOTIONAL.value,
        audience_segment=AudienceSegment.CUSTOMER.value,
        provider="mock_mail",
        status=CampaignStatus.ACTIVE.value,
        content_revision=1,
        approved_revision=1,
        delivery_mode=DeliveryMode.MOCK.value,
        sending_window_start="09:00",
        sending_window_end="17:00",
        min_gap_days=7,
        offer_id=offer.id,
        objective="Offer a relevant upgrade",
    )
    reengage = Campaign(
        id=uuid4(),
        name="Re-engagement",
        campaign_type=CampaignType.REENGAGEMENT.value,
        audience_segment=AudienceSegment.INACTIVE_SUBSCRIBER.value,
        provider="mock_mail",
        status=CampaignStatus.ACTIVE.value,
        content_revision=1,
        approved_revision=1,
        delivery_mode=DeliveryMode.MOCK.value,
        sending_window_start="09:00",
        sending_window_end="17:00",
        offer_id=offer.id,
        objective="Re-establish interest",
    )
    session.add_all([cold, onboarding, promo, reengage])
    session.flush()
    steps = [
        (cold, 1, 0, "Introduction with product value and a sales CTA.", "15-min chat?"),
        (cold, 2, 4, "Useful follow-up.", "Open to a look?"),
        (cold, 3, 6, "Final follow-up.", "Should I close the loop?"),
        (onboarding, 1, 0, "Welcome and activation.", "Try the queue"),
        (onboarding, 2, 3, "Product education.", "See one workflow"),
        (onboarding, 3, 4, "Relevant offer if not activated.", "Start the pilot"),
        (promo, 1, 0, "Promotion.", "Redeem the pilot"),
        (promo, 2, 7, "Reminder if still eligible.", "Last reminder"),
        (reengage, 1, 0, "Re-engagement.", "Still useful?"),
        (reengage, 2, 7, "Reminder.", "One more note"),
    ]
    for campaign, number, delay, template, cta in steps:
        session.add(
            CampaignStep(
                id=uuid4(),
                campaign_id=campaign.id,
                step_number=number,
                delay_days=delay,
                template=template,
                call_to_action=cta,
                offer_id=offer.id if campaign.campaign_type != CampaignType.COLD.value else None,
            )
        )

    rows = [
        {
            "email": "jordan.hale@example.com",
            "name": "Jordan Hale",
            "company": "Northline Logistics",
            "role": "VP of Operations",
            "segment": "prospect",
            "timezone": "America/Chicago",
            "audience_reviewed": True,
            "marketing_consent": False,
            "notes": "Synthetic prospect. Eligible for mock cold sequence.",
        },
        {
            "email": "sam.lee@example.com",
            "name": "Sam Lee",
            "company": "Northline Logistics",
            "role": "Dispatcher",
            "segment": "opted_in_user",
            "timezone": "America/New_York",
            "marketing_consent": True,
            "consent_source": "signup_checkbox",
        },
        {
            "email": "riley.chen@example.com",
            "name": "Riley Chen",
            "company": "Harborline Customer Co",
            "role": "Director of Ops",
            "segment": "customer",
            "timezone": "America/Los_Angeles",
            "marketing_consent": True,
            "consent_source": "account_settings",
        },
        {
            "email": "morgan.patel@example.com",
            "name": "Morgan Patel",
            "company": "Idle Fleet Inc",
            "role": "Manager",
            "segment": "inactive_subscriber",
            "timezone": "America/Chicago",
            "marketing_consent": True,
            "consent_source": "legacy_list",
        },
        {
            "email": "no-consent@example.com",
            "name": "No Consent",
            "company": "Example",
            "role": "Buyer",
            "segment": "customer",
            "timezone": "America/Chicago",
            "marketing_consent": False,
        },
        {
            "email": "unreviewed@example.com",
            "name": "Unreviewed Prospect",
            "company": "Unknown Co",
            "role": "Lead",
            "segment": "prospect",
            "country": "",
            "audience_reviewed": False,
            "marketing_consent": False,
        },
    ]
    import_contacts(session, rows, clock, actor="seed")

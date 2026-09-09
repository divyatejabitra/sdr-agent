from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock
from sdr_workspace.config import Settings
from sdr_workspace.enums import (
    CampaignType,
    EnrollmentStatus,
    JobStatus,
    JobType,
    MessageStatus,
)
from sdr_workspace.models import Campaign, CampaignStep, Contact, Enrollment, Message, Offer
from sdr_workspace.providers.llm import build_draft_prompt, get_llm
from sdr_workspace.providers.mail import MailAdapter, get_mail_adapter
from sdr_workspace.providers.sequencer import ColdSequencer, get_sequencer
from sdr_workspace.services.drafting import approved_knowledge, persist_draft, render_message, validate_draft
from sdr_workspace.services.eligibility import evaluate_eligibility
from sdr_workspace.services.enrollments import complete_enrollment, schedule_next_step
from sdr_workspace.services.jobs import complete
from sdr_workspace.services.tokens import unsubscribe_url
from sdr_workspace.models import Job


def dispatch_tick(session: Session, settings: Settings, clock: Clock) -> dict:
    from sdr_workspace.services.jobs import enqueue

    due = session.scalars(
        select(Enrollment).where(
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
            Enrollment.next_send_at <= clock.now(),
        )
    ).all()
    created = 0
    for enrollment in due:
        campaign = session.get(Campaign, enrollment.campaign_id)
        if campaign is None or campaign.campaign_type == CampaignType.COLD.value:
            continue
        if enrollment.delivery_mode != campaign.delivery_mode:
            continue
        job = enqueue(
            session,
            job_type=JobType.DRAFT_AND_SEND.value,
            idempotency_key=f"send:{enrollment.id}:{enrollment.current_step}:{enrollment.delivery_mode}",
            payload={"enrollment_id": str(enrollment.id), "step_number": enrollment.current_step},
            due_at=enrollment.next_send_at or clock.now(),
            delivery_mode=enrollment.delivery_mode,
        )
        if job.status == JobStatus.PENDING.value:
            created += 1
    return {"enqueued": created}


def enroll_ready_prospects(session: Session, settings: Settings, clock: Clock) -> dict:
    from sdr_workspace.services.enrollments import enroll_contact

    campaigns = session.scalars(
        select(Campaign).where(
            Campaign.campaign_type == CampaignType.COLD.value,
            Campaign.status == "active",
            Campaign.delivery_mode == settings.delivery_mode,
        )
    ).all()
    enrolled = 0
    errors = []
    for campaign in campaigns:
        contacts = session.scalars(
            select(Contact).where(Contact.segment == campaign.audience_segment, Contact.audience_reviewed.is_(True))
        ).all()
        for contact in contacts:
            try:
                enroll_contact(
                    session, contact=contact, campaign=campaign, settings=settings, clock=clock, actor="internal"
                )
                enrolled += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"contact": str(contact.id), "error": str(exc)})
    return {"enrolled": enrolled, "errors": errors}


def execute_job(
    session: Session,
    job: Job,
    settings: Settings,
    clock: Clock,
    *,
    mail: MailAdapter | None = None,
    sequencer: ColdSequencer | None = None,
    llm=None,
) -> None:
    if job.delivery_mode != settings.delivery_mode and job.delivery_mode != "mock":
        if settings.delivery_mode == "live" and job.delivery_mode != "live":
            complete(session, job, JobStatus.CANCELED.value, "mode_switch_blocked")
            return
    if job.job_type == JobType.DRAFT_AND_SEND.value:
        _send_customer(session, job, settings, clock, mail=mail, llm=llm)
    elif job.job_type == JobType.COLD_ENROLL.value:
        _enroll_cold(session, job, settings, clock, sequencer=sequencer)
    elif job.job_type == JobType.PROVIDER_STOP.value:
        _provider_stop(session, job, settings, sequencer=sequencer)
    else:
        complete(session, job, JobStatus.SUCCEEDED.value)


def _send_customer(session, job, settings, clock, mail=None, llm=None) -> None:
    enrollment = session.get(Enrollment, job.payload["enrollment_id"])
    if enrollment is None:
        complete(session, job, JobStatus.FAILED.value, "enrollment_missing")
        return
    if enrollment.status != EnrollmentStatus.ACTIVE.value:
        complete(session, job, JobStatus.CANCELED.value, "enrollment_not_active")
        return
    campaign = session.get(Campaign, enrollment.campaign_id)
    contact = session.get(Contact, enrollment.contact_id)
    step = session.scalar(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign.id,
            CampaignStep.step_number == int(job.payload["step_number"]),
        )
    )
    if not campaign or not contact or not step:
        complete(session, job, JobStatus.FAILED.value, "missing_context")
        return
    if campaign.campaign_type == CampaignType.COLD.value:
        complete(session, job, JobStatus.CANCELED.value, "cold_owned_by_sequencer")
        return
    eligibility = evaluate_eligibility(
        session, contact=contact, campaign=campaign, settings=settings, clock=clock, step=step, for_live_send=True
    )
    if not eligibility.allowed:
        if "outside_sending_window" in eligibility.reason_codes or "frequency_cap" in eligibility.reason_codes:
            job.due_at = eligibility.next_eligible_at or job.due_at
            job.status = JobStatus.PENDING.value
            job.lease_owner = ""
            return
        complete(session, job, JobStatus.CANCELED.value, ",".join(eligibility.reason_codes))
        return
    existing = session.scalar(
        select(Message).where(Message.enrollment_id == enrollment.id, Message.step_number == step.step_number)
    )
    if existing and existing.status in {MessageStatus.ACCEPTED.value, MessageStatus.SIMULATED.value, MessageStatus.DELIVERED.value}:
        complete(session, job, JobStatus.SUCCEEDED.value, "already_sent")
        return
    if existing and existing.status == MessageStatus.UNKNOWN.value:
        complete(session, job, JobStatus.UNKNOWN.value, "unresolved_prior_attempt")
        return
    knowledge = approved_knowledge(session)
    offer = session.get(Offer, step.offer_id or campaign.offer_id) if (step.offer_id or campaign.offer_id) else None
    llm = llm or get_llm(settings)
    prompt = build_draft_prompt(contact, campaign, step, knowledge, offer, "")
    draft = None
    for _ in range(2):
        try:
            draft = llm.draft(prompt)
            draft = validate_draft(draft, knowledge, settings.max_body_words)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            draft = None
    if draft is None:
        from sdr_workspace.services.drafting import DraftOutput

        draft = DraftOutput(
            subject="Needs review",
            body="Draft generation failed.",
            needs_review=True,
            review_reasons=["invalid_model_output"],
        )
    unsub = unsubscribe_url(settings, contact.id)
    subject, text, html = render_message(draft=draft, settings=settings, unsubscribe_url=unsub, offer=offer)
    message = existing or persist_draft(
        session,
        enrollment_id=enrollment.id,
        campaign=campaign,
        contact=contact,
        step=step,
        draft=draft,
        final_subject=subject,
        final_body=text,
        delivery_mode=enrollment.delivery_mode,
    )
    if draft.needs_review:
        message.status = MessageStatus.NEEDS_REVIEW.value
        complete(session, job, JobStatus.SUCCEEDED.value, "held_for_review")
        return
    message.status = MessageStatus.SUBMITTING.value
    session.flush()
    adapter = mail or get_mail_adapter(campaign.provider, settings)
    result = adapter.send(
        to_email=contact.original_email,
        subject=subject,
        text=text,
        html=html,
        correlation_id=message.correlation_id,
    )
    if result.status == "accepted":
        simulated = enrollment.delivery_mode == "mock"
        message.status = MessageStatus.SIMULATED.value if simulated else MessageStatus.ACCEPTED.value
        message.provider_message_id = result.provider_id
        message.dispatched_at = clock.now()
        contact.last_marketing_sent_at = clock.now()
        schedule_next_step(
            session, enrollment=enrollment, campaign=campaign, contact=contact, clock=clock, dispatched_at=clock.now()
        )
        complete(session, job, JobStatus.SUCCEEDED.value)
    elif result.status == "unknown":
        message.status = MessageStatus.UNKNOWN.value
        enrollment.status = EnrollmentStatus.UNKNOWN.value
        complete(session, job, JobStatus.UNKNOWN.value, result.error)
    else:
        message.status = MessageStatus.FAILED.value
        if result.retryable and job.attempt_count < 5:
            job.status = JobStatus.PENDING.value
            job.last_error = result.error
            job.lease_owner = ""
        else:
            complete(session, job, JobStatus.FAILED.value, result.error)


def _enroll_cold(session, job, settings, clock, sequencer=None) -> None:
    enrollment = session.get(Enrollment, job.payload["enrollment_id"])
    campaign = session.get(Campaign, enrollment.campaign_id)
    contact = session.get(Contact, enrollment.contact_id)
    adapter = sequencer or get_sequencer(campaign.provider, settings)
    if enrollment.external_lead_id:
        state = adapter.status(campaign.external_campaign_id or "mock-cold", enrollment.external_lead_id)
        if state.status == "accepted":
            complete(session, job, JobStatus.SUCCEEDED.value, "already_enrolled")
            return
    lead = {
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "email": contact.original_email,
        "company_name": contact.company,
        "custom_fields": {"job_title": contact.role},
    }
    result = adapter.enroll(campaign.external_campaign_id or "mock-cold", lead)
    if result.status == "accepted":
        enrollment.external_lead_id = result.external_id
        enrollment.provider_sync_status = "synced"
        complete(session, job, JobStatus.SUCCEEDED.value)
    elif result.status == "unknown":
        enrollment.status = EnrollmentStatus.UNKNOWN.value
        complete(session, job, JobStatus.UNKNOWN.value, result.error)
    else:
        complete(session, job, JobStatus.FAILED.value, result.error)


def _provider_stop(session, job, settings, sequencer=None) -> None:
    enrollment = session.get(Enrollment, job.payload.get("enrollment_id"))
    if enrollment is None or not enrollment.external_lead_id:
        complete(session, job, JobStatus.SUCCEEDED.value, "nothing_to_stop")
        return
    campaign = session.get(Campaign, enrollment.campaign_id)
    adapter = sequencer or get_sequencer(campaign.provider, settings)
    result = adapter.unsubscribe(enrollment.external_lead_id)
    if result.status == "accepted":
        enrollment.provider_sync_status = "stopped"
        if enrollment.status == EnrollmentStatus.PENDING_CANCELLATION.value:
            enrollment.status = EnrollmentStatus.CANCELED.value
        complete(session, job, JobStatus.SUCCEEDED.value)
    else:
        enrollment.provider_sync_status = "stop_failed"
        complete(session, job, JobStatus.FAILED.value, result.error)

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock
from sdr_workspace.config import Settings
from sdr_workspace.enums import CampaignType, EnrollmentStatus, JobType, MessageStatus
from sdr_workspace.models import AuditLog, Campaign, CampaignStep, Contact, Enrollment, Message
from sdr_workspace.providers.sequencer import ColdSequencer
from sdr_workspace.services.eligibility import evaluate_eligibility
from sdr_workspace.services.jobs import enqueue
from sdr_workspace.services.scheduling import add_delay_in_window, next_in_window


def enroll_contact(
    session: Session,
    *,
    contact: Contact,
    campaign: Campaign,
    settings: Settings,
    clock: Clock,
    actor: str,
    sequencer: ColdSequencer | None = None,
) -> Enrollment:
    result = evaluate_eligibility(session, contact=contact, campaign=campaign, settings=settings, clock=clock)
    if not result.allowed:
        raise ValueError(",".join(result.reason_codes))
    enrollment = Enrollment(
        id=uuid4(),
        contact_id=contact.id,
        campaign_id=campaign.id,
        delivery_mode=campaign.delivery_mode,
        status=EnrollmentStatus.ACTIVE.value,
        current_step=1,
        next_send_at=next_in_window(
            clock.now(), contact.timezone, campaign.sending_window_start, campaign.sending_window_end
        ),
        provider_sync_status="local",
    )
    session.add(enrollment)
    session.flush()
    if campaign.campaign_type == CampaignType.COLD.value:
        enqueue(
            session,
            job_type=JobType.COLD_ENROLL.value,
            idempotency_key=f"cold-enroll:{enrollment.id}:{campaign.delivery_mode}",
            payload={"enrollment_id": str(enrollment.id)},
            due_at=clock.now(),
            delivery_mode=campaign.delivery_mode,
        )
        enrollment.provider_sync_status = "pending_provider"
    else:
        enqueue(
            session,
            job_type=JobType.DRAFT_AND_SEND.value,
            idempotency_key=f"send:{enrollment.id}:1:{campaign.delivery_mode}",
            payload={"enrollment_id": str(enrollment.id), "step_number": 1},
            due_at=enrollment.next_send_at,
            delivery_mode=campaign.delivery_mode,
        )
    session.add(
        AuditLog(
            id=uuid4(),
            actor=actor,
            action="enrollment.create",
            entity_type="enrollment",
            entity_id=str(enrollment.id),
            details={"campaign_id": str(campaign.id)},
        )
    )
    return enrollment


def pause_enrollment(session: Session, enrollment: Enrollment, reason: str, clock: Clock) -> None:
    if enrollment.status in {EnrollmentStatus.COMPLETED.value, EnrollmentStatus.CANCELED.value}:
        return
    enrollment.status = EnrollmentStatus.PAUSED.value
    enrollment.stop_reason = reason
    enrollment.updated_at = clock.now()
    _cancel_pending_sends(session, enrollment)


def complete_enrollment(session: Session, enrollment: Enrollment, reason: str, clock: Clock) -> None:
    enrollment.status = EnrollmentStatus.COMPLETED.value
    enrollment.stop_reason = reason
    enrollment.next_send_at = None
    enrollment.updated_at = clock.now()
    _cancel_pending_sends(session, enrollment)


def _cancel_pending_sends(session: Session, enrollment: Enrollment) -> None:
    from sdr_workspace.models import Job
    from sqlalchemy import select
    from sdr_workspace.enums import JobStatus

    jobs = session.scalars(
        select(Job).where(
            Job.status.in_([JobStatus.PENDING.value, JobStatus.LEASED.value]),
            Job.job_type == JobType.DRAFT_AND_SEND.value,
        )
    ).all()
    for job in jobs:
        if job.payload.get("enrollment_id") == str(enrollment.id):
            job.status = JobStatus.CANCELED.value
    messages = session.scalars(
        select(Message).where(
            Message.enrollment_id == enrollment.id,
            Message.status.in_([MessageStatus.DRAFTED.value, MessageStatus.READY.value, MessageStatus.NEEDS_REVIEW.value]),
        )
    ).all()
    for message in messages:
        if message.status != MessageStatus.ACCEPTED.value:
            message.status = MessageStatus.CANCELED.value


def schedule_next_step(
    session: Session,
    *,
    enrollment: Enrollment,
    campaign: Campaign,
    contact: Contact,
    clock: Clock,
    dispatched_at,
) -> None:
    next_number = enrollment.current_step + 1
    step = session.scalar(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign.id,
            CampaignStep.step_number == next_number,
        )
    )
    if step is None:
        complete_enrollment(session, enrollment, "sequence_exhausted", clock)
        return
    enrollment.current_step = next_number
    delay = max(step.delay_days, campaign.min_gap_days)
    enrollment.next_send_at = add_delay_in_window(
        clock,
        contact.timezone,
        campaign.sending_window_start,
        campaign.sending_window_end,
        delay,
        from_time=dispatched_at,
    )
    enrollment.status = EnrollmentStatus.ACTIVE.value
    enqueue(
        session,
        job_type=JobType.DRAFT_AND_SEND.value,
        idempotency_key=f"send:{enrollment.id}:{next_number}:{enrollment.delivery_mode}",
        payload={"enrollment_id": str(enrollment.id), "step_number": next_number},
        due_at=enrollment.next_send_at,
        delivery_mode=enrollment.delivery_mode,
    )

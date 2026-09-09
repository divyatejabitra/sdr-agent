from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock
from sdr_workspace.config import Settings
from sdr_workspace.enums import (
    ConsentStatus,
    EnrollmentStatus,
    JobType,
    MessageStatus,
    ReplyIntent,
    SuppressionScope,
)
from sdr_workspace.models import Contact, Enrollment, InboundReply, Message, ProviderEvent, SalesTask, Suppression
from sdr_workspace.providers.llm import get_llm
from sdr_workspace.services.enrollments import complete_enrollment, pause_enrollment
from sdr_workspace.services.jobs import enqueue
from sdr_workspace.services.email_norm import email_key


def persist_event(
    session: Session,
    *,
    provider: str,
    external_event_id: str,
    event_type: str,
    payload: dict,
    occurred_at: datetime,
    clock: Clock,
    delivery_mode: str,
) -> ProviderEvent | None:
    existing = session.scalar(
        select(ProviderEvent).where(
            ProviderEvent.provider == provider, ProviderEvent.external_event_id == external_event_id
        )
    )
    if existing:
        return None
    event = ProviderEvent(
        id=uuid4(),
        provider=provider,
        external_event_id=external_event_id,
        event_type=event_type,
        payload=payload,
        occurred_at=occurred_at,
        received_at=clock.now(),
        processing_status="pending",
        delivery_mode=delivery_mode,
    )
    session.add(event)
    session.flush()
    return event


def apply_event(session: Session, event: ProviderEvent, settings: Settings, clock: Clock) -> None:
    payload = event.payload
    contact = _contact_from_payload(session, payload)
    if event.event_type in {"unsubscribe", "spamcomplaint", "spamreport"}:
        if contact:
            suppress(session, contact, scope=SuppressionScope.MARKETING.value, reason=event.event_type, source=event.provider, clock=clock, settings=settings)
        event.processing_status = "processed"
        return
    if event.event_type in {"bounce", "dropped"}:
        if contact:
            suppress(session, contact, scope=SuppressionScope.GLOBAL.value, reason="hard_bounce", source=event.provider, clock=clock, settings=settings)
            _mark_message(session, payload, MessageStatus.FAILED.value)
        event.processing_status = "processed"
        return
    if event.event_type in {"delivered"}:
        _mark_message(session, payload, MessageStatus.DELIVERED.value)
        event.processing_status = "processed"
        return
    if event.event_type in {"deferred"}:
        event.processing_status = "ignored"
        return
    event.processing_status = "processed"


def suppress(
    session: Session,
    contact: Contact,
    *,
    scope: str,
    reason: str,
    source: str,
    clock: Clock,
    settings: Settings,
) -> Suppression:
    row = Suppression(
        id=uuid4(),
        contact_id=contact.id,
        email_key=contact.email_key,
        scope=scope,
        reason=reason,
        source=source,
        provider_sync_status="pending",
    )
    session.add(row)
    from sdr_workspace.models import ConsentEvent

    session.add(
        ConsentEvent(
            id=uuid4(),
            contact_id=contact.id,
            topic="marketing",
            status=ConsentStatus.WITHDRAWN.value,
            occurred_at=clock.now(),
            source=source,
            evidence_ref=reason,
        )
    )
    enrollments = session.scalars(
        select(Enrollment).where(
            Enrollment.contact_id == contact.id,
            Enrollment.status.in_(
                [
                    EnrollmentStatus.ACTIVE.value,
                    EnrollmentStatus.PAUSED.value,
                    EnrollmentStatus.UNKNOWN.value,
                ]
            ),
        )
    ).all()
    for enrollment in enrollments:
        enrollment.status = EnrollmentStatus.PENDING_CANCELLATION.value
        enrollment.stop_reason = reason
        pause_enrollment(session, enrollment, reason, clock)
        enrollment.status = EnrollmentStatus.PENDING_CANCELLATION.value
        enqueue(
            session,
            job_type=JobType.PROVIDER_STOP.value,
            idempotency_key=f"stop:{enrollment.id}:{reason}",
            payload={"enrollment_id": str(enrollment.id)},
            due_at=clock.now(),
            delivery_mode=enrollment.delivery_mode,
        )
    return row


def ingest_reply(
    session: Session,
    *,
    from_email: str,
    subject: str,
    body: str,
    quoted: str,
    thread_id: str,
    message_id_header: str,
    settings: Settings,
    clock: Clock,
    llm=None,
) -> InboundReply:
    contact = session.scalar(select(Contact).where(Contact.email_key == email_key(from_email)))
    reply = InboundReply(
        id=uuid4(),
        contact_id=contact.id if contact else None,
        from_email=from_email,
        subject=subject,
        newest_content=body,
        quoted_content=quoted,
        thread_id=thread_id,
        message_id_header=message_id_header,
        paused_before_classify=False,
    )
    session.add(reply)
    if contact:
        enrollments = session.scalars(
            select(Enrollment).where(
                Enrollment.contact_id == contact.id,
                Enrollment.status == EnrollmentStatus.ACTIVE.value,
            )
        ).all()
        for enrollment in enrollments:
            pause_enrollment(session, enrollment, "reply_received", clock)
        reply.paused_before_classify = True
        explicit = _explicit_opt_out(body, quoted)
        if explicit:
            suppress(
                session,
                contact,
                scope=SuppressionScope.MARKETING.value,
                reason="unsubscribe",
                source="inbox",
                clock=clock,
                settings=settings,
            )
            reply.intent = ReplyIntent.UNSUBSCRIBE.value
            return reply
        try:
            llm = llm or get_llm(settings)
            classified = llm.classify(body, quoted)
            if isinstance(classified, str):
                import json

                classified = json.loads(classified)
            reply.intent = classified.get("intent") or ReplyIntent.UNKNOWN.value
            if reply.intent == ReplyIntent.UNSUBSCRIBE.value:
                suppress(
                    session,
                    contact,
                    scope=SuppressionScope.MARKETING.value,
                    reason="unsubscribe",
                    source="classifier",
                    clock=clock,
                    settings=settings,
                )
            elif reply.intent in {ReplyIntent.INTERESTED.value, ReplyIntent.QUESTION.value}:
                session.add(
                    SalesTask(
                        id=uuid4(),
                        contact_id=contact.id,
                        task_type="reply_follow_up",
                        summary=classified.get("summary") or body[:200],
                        suggested_reply="Thanks for the reply — happy to share a 15-minute walkthrough this week.",
                        intent=reply.intent,
                    )
                )
            elif reply.intent in {ReplyIntent.NEGATIVE.value, ReplyIntent.WRONG_PERSON.value}:
                for enrollment in session.scalars(
                    select(Enrollment).where(Enrollment.contact_id == contact.id)
                ):
                    if enrollment.status != EnrollmentStatus.COMPLETED.value:
                        complete_enrollment(session, enrollment, reply.intent, clock)
            elif reply.intent == ReplyIntent.NOT_NOW.value:
                for enrollment in enrollments:
                    enrollment.requested_resume_at = clock.now() + timedelta(days=30)
        except Exception:
            reply.intent = ReplyIntent.UNKNOWN.value
    return reply


def record_conversion(session: Session, contact: Contact, kind: str, source: str, clock: Clock, notes: str = "") -> None:
    from sdr_workspace.models import Conversion

    session.add(
        Conversion(
            id=uuid4(),
            contact_id=contact.id,
            kind=kind,
            source=source,
            occurred_at=clock.now(),
            notes=notes,
        )
    )
    contact.lifecycle_status = "converted"
    contact.last_purchase_at = clock.now()
    for enrollment in session.scalars(select(Enrollment).where(Enrollment.contact_id == contact.id)):
        campaign_type = enrollment.stop_reason
        complete_enrollment(session, enrollment, f"converted:{kind}", clock)


def _explicit_opt_out(newest: str, quoted: str) -> bool:
    text = newest.lower()
    if "unsubscribe" in quoted.lower() and "unsubscribe" not in text and "stop" not in text:
        return False
    return any(token in text for token in ("unsubscribe", "stop emailing", "remove me"))


def _contact_from_payload(session: Session, payload: dict) -> Contact | None:
    email = payload.get("email") or payload.get("from") or ""
    cid = payload.get("cid") or (payload.get("unique_args") or {}).get("cid")
    if cid:
        message = session.scalar(select(Message).where(Message.correlation_id == cid))
        if message:
            return session.get(Contact, message.contact_id)
    if email:
        return session.scalar(select(Contact).where(Contact.email_key == email_key(email)))
    return None


def _mark_message(session: Session, payload: dict, status: str) -> None:
    cid = payload.get("cid") or (payload.get("unique_args") or {}).get("cid")
    provider_id = payload.get("sg_message_id") or payload.get("message_id")
    message = None
    if cid:
        message = session.scalar(select(Message).where(Message.correlation_id == cid))
    if message is None and provider_id:
        message = session.scalar(select(Message).where(Message.provider_message_id == str(provider_id)))
    if message is None:
        return
    if message.status in {MessageStatus.CANCELED.value}:
        return
    contact = session.get(Contact, message.contact_id)
    enrollment = session.get(Enrollment, message.enrollment_id)
    if status == MessageStatus.FAILED.value and enrollment and enrollment.status in {
        EnrollmentStatus.CANCELED.value,
        EnrollmentStatus.PENDING_CANCELLATION.value,
        EnrollmentStatus.COMPLETED.value,
    }:
        message.status = status
        return
    message.status = status
    if status == MessageStatus.DELIVERED.value:
        message.delivered_at = message.delivered_at or payload.get("occurred_at")

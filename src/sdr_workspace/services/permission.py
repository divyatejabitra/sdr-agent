from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdr_workspace.enums import ConsentStatus, ConsentTopic
from sdr_workspace.models import ConsentEvent, Contact, Suppression


def current_consent(session: Session, contact_id, topic: str = ConsentTopic.MARKETING.value) -> ConsentEvent | None:
    return session.scalar(
        select(ConsentEvent)
        .where(ConsentEvent.contact_id == contact_id, ConsentEvent.topic == topic)
        .order_by(ConsentEvent.occurred_at.desc(), ConsentEvent.created_at.desc())
        .limit(1)
    )


def has_marketing_consent(session: Session, contact: Contact, now: datetime) -> bool:
    event = current_consent(session, contact.id, ConsentTopic.MARKETING.value)
    if event is None or event.status != ConsentStatus.GRANTED.value:
        return False
    if event.expires_at and event.expires_at <= now:
        return False
    return True


def is_suppressed(session: Session, contact: Contact, scopes: list[str]) -> Suppression | None:
    rows = session.scalars(
        select(Suppression).where(
            Suppression.email_key == contact.email_key,
            Suppression.scope.in_(scopes + ["global"]),
        )
    ).all()
    return rows[0] if rows else None

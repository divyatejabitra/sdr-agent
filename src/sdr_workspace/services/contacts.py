from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock
from sdr_workspace.config import Settings
from sdr_workspace.enums import ConsentStatus
from sdr_workspace.models import AuditLog, ConsentEvent, Contact
from sdr_workspace.services.email_norm import email_key, split_name


def import_contacts(session: Session, rows: list[dict], clock: Clock, actor: str) -> dict:
    created, updated, errors = [], [], []
    for index, row in enumerate(rows):
        try:
            email = (row.get("email") or row.get("original_email") or "").strip()
            if not email or "@" not in email:
                raise ValueError("invalid_email")
            key = email_key(email)
            contact = session.scalar(select(Contact).where(Contact.email_key == key))
            first, last = split_name(row.get("name") or "")
            first = row.get("first_name") or first
            last = row.get("last_name") or last
            fields = {
                "original_email": email,
                "email_key": key,
                "first_name": first,
                "last_name": last,
                "company": row.get("company") or "",
                "role": row.get("role") or row.get("title") or "",
                "segment": row.get("segment") or "prospect",
                "country": row.get("country") or "US",
                "timezone": row.get("timezone") or "America/Chicago",
                "source": row.get("source") or "import",
                "audience_reviewed": bool(row.get("audience_reviewed", False)),
                "notes": row.get("notes") or "",
            }
            if contact is None:
                contact = Contact(id=uuid4(), **fields)
                session.add(contact)
                session.flush()
                created.append(str(contact.id))
            else:
                for name, value in fields.items():
                    if name in {"original_email", "email_key"}:
                        continue
                    setattr(contact, name, value)
                updated.append(str(contact.id))
            if row.get("marketing_consent") is True:
                _record_consent(
                    session,
                    contact,
                    status=ConsentStatus.GRANTED.value,
                    source=row.get("consent_source") or "import",
                    clock=clock,
                )
            elif row.get("marketing_consent") is False:
                _record_consent(
                    session,
                    contact,
                    status=ConsentStatus.WITHDRAWN.value,
                    source=row.get("consent_source") or "import",
                    clock=clock,
                )
        except Exception as exc:  # noqa: BLE001 — collect per-row import errors
            errors.append({"row": index, "error": str(exc)})
    session.add(
        AuditLog(
            id=uuid4(),
            actor=actor,
            action="contacts.import",
            entity_type="contact",
            entity_id="batch",
            details={"created": len(created), "updated": len(updated), "errors": len(errors)},
        )
    )
    return {"created": created, "updated": updated, "errors": errors}


def _record_consent(session: Session, contact: Contact, status: str, source: str, clock: Clock) -> None:
    existing = session.scalars(
        select(ConsentEvent)
        .where(ConsentEvent.contact_id == contact.id, ConsentEvent.topic == "marketing")
        .order_by(ConsentEvent.occurred_at.desc())
        .limit(1)
    ).first()
    if existing and existing.status == ConsentStatus.WITHDRAWN.value and status == ConsentStatus.GRANTED.value:
        if source == "import":
            return
    session.add(
        ConsentEvent(
            id=uuid4(),
            contact_id=contact.id,
            topic="marketing",
            status=status,
            occurred_at=clock.now(),
            source=source,
            evidence_ref=f"{source}:{contact.email_key}",
        )
    )


def record_consent(
    session: Session,
    contact: Contact,
    *,
    status: str,
    topic: str,
    source: str,
    clock: Clock,
    evidence_ref: str = "",
) -> ConsentEvent:
    event = ConsentEvent(
        id=uuid4(),
        contact_id=contact.id,
        topic=topic,
        status=status,
        occurred_at=clock.now(),
        source=source,
        evidence_ref=evidence_ref or source,
    )
    session.add(event)
    session.flush()
    return event

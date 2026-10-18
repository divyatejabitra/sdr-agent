from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from uuid import UUID, uuid4
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdr_workspace.api.deps import current_settings, get_clock, get_db, require_internal, require_owner
from sdr_workspace.clock import Clock
from sdr_workspace.config import Settings
from sdr_workspace.enums import CampaignStatus, CampaignType, JobStatus, KnowledgeStatus, OfferStatus
from sdr_workspace.models import (
    AppSetting,
    AuditLog,
    Campaign,
    CampaignStep,
    ConsentEvent,
    Contact,
    Conversion,
    Enrollment,
    Job,
    KnowledgeSource,
    Message,
    Offer,
    OwnerUser,
    SalesTask,
    Suppression,
)
from sdr_workspace.providers.hubspot import HubSpotAdapter
from sdr_workspace.services.contacts import import_contacts, record_consent
from sdr_workspace.services.dispatch import dispatch_tick, draft_message, enroll_ready_prospects, execute_job
from sdr_workspace.services.eligibility import evaluate_eligibility, explain_contact
from sdr_workspace.services.enrollments import complete_enrollment, enroll_contact, pause_enrollment
from sdr_workspace.services.events import apply_event, ingest_reply, persist_event, record_conversion, suppress
from sdr_workspace.services.jobs import claim_jobs
from sdr_workspace.services.security import session_serializer, verify_password
from sdr_workspace.services.tokens import parse_unsubscribe_token
from sdr_workspace.providers.sendgrid_verify import verify_sendgrid_signature
from sdr_workspace.models import InboundReply, ProviderEvent


router = APIRouter()
public = APIRouter()
internal = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)])
webhooks = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


class ContactIn(BaseModel):
    email: str
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    role: str = ""
    segment: str = "prospect"
    country: str = "US"
    timezone: str = "America/Chicago"
    source: str = "ui"
    audience_reviewed: bool = False
    marketing_consent: bool | None = None
    notes: str = ""


class ConsentIn(BaseModel):
    status: str
    topic: str = "marketing"
    source: str
    evidence_ref: str = ""


class CampaignIn(BaseModel):
    name: str
    campaign_type: str
    audience_segment: str
    provider: str
    sending_window_start: str = "09:00"
    sending_window_end: str = "17:00"
    min_gap_days: int = 0
    objective: str = ""
    offer_id: UUID | None = None
    steps: list[dict] = Field(default_factory=list)


class EnrollIn(BaseModel):
    contact_id: UUID
    campaign_id: UUID


class OfferIn(BaseModel):
    name: str
    product: str
    price_text: str
    currency: str = "USD"
    terms: str
    destination_url: str
    eligibility: str = ""
    code: str = ""


class KnowledgeIn(BaseModel):
    title: str
    body: str
    evidence_id: str
    source_ref: str = "internal"


class SuppressionIn(BaseModel):
    email: str
    scope: str = "marketing"
    reason: str


class ConversionIn(BaseModel):
    contact_id: UUID
    kind: str
    notes: str = ""


class MessageEdit(BaseModel):
    subject: str | None = None
    body: str | None = None
    approve: bool = False


class InboxEvent(BaseModel):
    from_email: str
    subject: str = ""
    newest_content: str
    quoted_content: str = ""
    thread_id: str = ""
    message_id_header: str = ""


def _page(items, total, offset):
    return {"items": items, "total": total, "offset": offset}


@public.post("/api/auth/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    user = db.scalar(select(OwnerUser).where(OwnerUser.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail={"code": "invalid_credentials"})
    token = session_serializer(settings).dumps({"email": user.email})
    response.set_cookie("sdr_session", token, httponly=True, samesite="lax")
    return {"ok": True, "email": user.email}


@public.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie("sdr_session")
    return {"ok": True}


@router.get("/api/me")
def me(user: OwnerUser = Depends(require_owner), settings: Settings = Depends(current_settings), db: Session = Depends(get_db)):
    from sdr_workspace.providers.llm import get_llm

    llm = get_llm(settings)
    hs = HubSpotAdapter(settings)
    return {
        "email": user.email,
        "app_name": settings.app_name,
        "delivery_mode": settings.delivery_mode,
        "business_name": settings.business_name,
        "integrations": {
            "llm": llm.name,
            "sendgrid": "configured" if settings.sendgrid_api_key else "disconnected",
            "smartlead": "configured" if settings.smartlead_api_key else "disconnected",
            "hubspot": hs.status().detail,
            "ollama": "selectable" if settings.llm_provider == "ollama" else "not_selected",
            "crewai": "selectable" if settings.llm_provider == "crewai" else "not_selected",
        },
    }


@router.get("/api/contacts")
def list_contacts(
    q: str = "",
    segment: str = "",
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: OwnerUser = Depends(require_owner),
):
    stmt = select(Contact)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Contact.original_email.ilike(like) | Contact.company.ilike(like) | Contact.first_name.ilike(like))
    if segment:
        stmt = stmt.where(Contact.segment == segment)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Contact.created_at.desc()).offset(offset).limit(limit)).all()
    return _page([_contact_out(db, row) for row in rows], total, offset)


@router.post("/api/contacts")
def create_contact(body: ContactIn, db: Session = Depends(get_db), clock: Clock = Depends(get_clock), user: OwnerUser = Depends(require_owner)):
    result = import_contacts(db, [body.model_dump()], clock, user.email)
    if result["errors"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/api/contacts/import")
def import_rows(rows: list[dict[str, Any]], db: Session = Depends(get_db), clock: Clock = Depends(get_clock), user: OwnerUser = Depends(require_owner)):
    return import_contacts(db, rows, clock, user.email)


@router.get("/api/contacts/{contact_id}")
def contact_detail(contact_id: UUID, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, detail={"code": "not_found"})
    timeline = db.scalars(select(ConsentEvent).where(ConsentEvent.contact_id == contact_id).order_by(ConsentEvent.occurred_at.desc())).all()
    return {
        **_contact_out(db, contact),
        "consent": [
            {"status": e.status, "topic": e.topic, "source": e.source, "occurred_at": e.occurred_at.isoformat()}
            for e in timeline
        ],
    }


@router.get("/api/contacts/{contact_id}/eligibility")
def contact_eligibility(
    contact_id: UUID,
    campaign_id: UUID | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    clock: Clock = Depends(get_clock),
    _: OwnerUser = Depends(require_owner),
):
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    result = explain_contact(db, contact_id, settings, clock, campaign)
    return {"allowed": result.allowed, "reason_codes": result.reason_codes, "next_eligible_at": result.next_eligible_at}


@router.post("/api/contacts/{contact_id}/consent")
def add_consent(contact_id: UUID, body: ConsentIn, db: Session = Depends(get_db), clock: Clock = Depends(get_clock), user: OwnerUser = Depends(require_owner)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, detail={"code": "not_found"})
    event = record_consent(db, contact, status=body.status, topic=body.topic, source=body.source, clock=clock, evidence_ref=body.evidence_ref)
    return {"id": str(event.id)}


@router.get("/api/campaigns")
def list_campaigns(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    rows = db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()
    return {"items": [_campaign_out(db, row) for row in rows]}


@router.post("/api/campaigns")
def create_campaign(body: CampaignIn, db: Session = Depends(get_db), settings: Settings = Depends(current_settings), user: OwnerUser = Depends(require_owner)):
    campaign = Campaign(
        id=uuid4(),
        name=body.name,
        campaign_type=body.campaign_type,
        audience_segment=body.audience_segment,
        provider=body.provider,
        sending_window_start=body.sending_window_start,
        sending_window_end=body.sending_window_end,
        min_gap_days=body.min_gap_days,
        objective=body.objective,
        offer_id=body.offer_id,
        delivery_mode=settings.delivery_mode,
        status=CampaignStatus.DRAFT.value,
    )
    db.add(campaign)
    db.flush()
    for step in body.steps:
        db.add(
            CampaignStep(
                id=uuid4(),
                campaign_id=campaign.id,
                step_number=int(step["step_number"]),
                delay_days=int(step.get("delay_days") or 0),
                template=step.get("template") or "",
                call_to_action=step.get("call_to_action") or "",
                offer_id=UUID(step["offer_id"]) if step.get("offer_id") else body.offer_id,
            )
        )
    return _campaign_out(db, campaign)


@router.post("/api/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: UUID, db: Session = Depends(get_db), user: OwnerUser = Depends(require_owner)):
    campaign = db.get(Campaign, campaign_id)
    campaign.approved_revision = campaign.content_revision
    campaign.status = CampaignStatus.APPROVED.value
    return _campaign_out(db, campaign)


@router.post("/api/campaigns/{campaign_id}/activate")
def activate_campaign(
    campaign_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    user: OwnerUser = Depends(require_owner),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign.approved_revision != campaign.content_revision:
        raise HTTPException(400, detail={"code": "unapproved_revision"})
    if settings.delivery_mode == "live" and campaign.delivery_mode != "live":
        raise HTTPException(400, detail={"code": "mode_mismatch"})
    campaign.status = CampaignStatus.ACTIVE.value
    return _campaign_out(db, campaign)


@router.post("/api/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: UUID, db: Session = Depends(get_db), user: OwnerUser = Depends(require_owner)):
    campaign = db.get(Campaign, campaign_id)
    campaign.status = CampaignStatus.PAUSED.value
    return _campaign_out(db, campaign)


@router.get("/api/enrollments")
def list_enrollments(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    rows = db.scalars(select(Enrollment).order_by(Enrollment.created_at.desc()).limit(200)).all()
    return {"items": [_enrollment_out(row) for row in rows]}


@router.post("/api/enrollments")
def create_enrollment(
    body: EnrollIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    clock: Clock = Depends(get_clock),
    user: OwnerUser = Depends(require_owner),
):
    contact = db.get(Contact, body.contact_id)
    campaign = db.get(Campaign, body.campaign_id)
    try:
        enrollment = enroll_contact(db, contact=contact, campaign=campaign, settings=settings, clock=clock, actor=user.email)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "ineligible", "reasons": str(exc).split(",")}) from exc
    return _enrollment_out(enrollment)


@router.post("/api/enrollments/{enrollment_id}/draft-now")
def draft_now(
    enrollment_id: UUID,
    step_number: int | None = Query(default=None, description="Defaults to the enrollment's current step"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    _: OwnerUser = Depends(require_owner),
):
    """Synchronously draft (never sends, never persists a Message row) using
    whichever provider LLM_PROVIDER currently selects - mock/ollama/crewai.
    For testing a provider's output on demand, without waiting for
    dispatch_tick + the worker's normal cadence to pick the enrollment up.
    """
    enrollment = db.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail={"code": "enrollment_not_found"})
    campaign = db.get(Campaign, enrollment.campaign_id)
    contact = db.get(Contact, enrollment.contact_id)
    if campaign is None or contact is None:
        raise HTTPException(status_code=404, detail={"code": "missing_context"})
    if campaign.campaign_type == CampaignType.COLD.value:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "cold_has_no_llm_draft",
                "message": "Cold campaigns are enrolled via the sequencer and never drafted by an LLM.",
            },
        )
    step = db.scalar(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign.id,
            CampaignStep.step_number == (step_number if step_number is not None else enrollment.current_step),
        )
    )
    if step is None:
        raise HTTPException(status_code=404, detail={"code": "step_not_found"})

    draft, subject, text, html, provider = draft_message(db, settings, contact=contact, campaign=campaign, step=step)
    return {
        "provider": provider,
        "enrollment_id": str(enrollment.id),
        "step_number": step.step_number,
        "subject": subject,
        "text": text,
        "html": html,
        "needs_review": draft.needs_review,
        "review_reasons": draft.review_reasons,
    }


@router.post("/api/enrollments/{enrollment_id}/pause")
def pause_enroll(enrollment_id: UUID, db: Session = Depends(get_db), clock: Clock = Depends(get_clock), user: OwnerUser = Depends(require_owner)):
    enrollment = db.get(Enrollment, enrollment_id)
    pause_enrollment(db, enrollment, "owner_pause", clock)
    return _enrollment_out(enrollment)


@router.post("/api/enrollments/{enrollment_id}/complete")
def complete_enroll(enrollment_id: UUID, db: Session = Depends(get_db), clock: Clock = Depends(get_clock), user: OwnerUser = Depends(require_owner)):
    enrollment = db.get(Enrollment, enrollment_id)
    complete_enrollment(db, enrollment, "owner_complete", clock)
    return _enrollment_out(enrollment)


@router.get("/api/offers")
def list_offers(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    return {"items": [_offer_out(row) for row in db.scalars(select(Offer)).all()]}


@router.post("/api/offers")
def create_offer(body: OfferIn, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    offer = Offer(id=uuid4(), status=OfferStatus.DRAFT.value, **body.model_dump())
    db.add(offer)
    db.flush()
    return _offer_out(offer)


@router.post("/api/offers/{offer_id}/approve")
def approve_offer(offer_id: UUID, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    offer = db.get(Offer, offer_id)
    offer.status = OfferStatus.APPROVED.value
    return _offer_out(offer)


@router.get("/api/knowledge")
def list_knowledge(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    return {"items": [_knowledge_out(row) for row in db.scalars(select(KnowledgeSource)).all()]}


@router.post("/api/knowledge")
def create_knowledge(body: KnowledgeIn, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    row = KnowledgeSource(id=uuid4(), status=KnowledgeStatus.DRAFT.value, **body.model_dump())
    db.add(row)
    db.flush()
    return _knowledge_out(row)


@router.post("/api/knowledge/{kid}/approve")
def approve_knowledge(kid: UUID, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    row = db.get(KnowledgeSource, kid)
    row.status = KnowledgeStatus.APPROVED.value
    return _knowledge_out(row)


@router.get("/api/messages")
def list_messages(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    rows = db.scalars(select(Message).order_by(Message.created_at.desc()).limit(200)).all()
    return {"items": [_message_out(row) for row in rows]}


@router.post("/api/messages/{message_id}")
def edit_message(message_id: UUID, body: MessageEdit, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    message = db.get(Message, message_id)
    if body.subject or body.body:
        if body.subject:
            message.final_subject = body.subject
            message.draft_subject = body.subject
        if body.body:
            message.final_body = body.body
            message.draft_body = body.body
        message.needs_review = True
        message.status = "needs_review"
        message.review_reasons = ["edited_after_draft"]
    if body.approve:
        message.needs_review = False
        message.status = "ready"
        message.review_reasons = []
    return _message_out(message)


@router.get("/api/sales-tasks")
def sales_tasks(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    rows = db.scalars(select(SalesTask).order_by(SalesTask.created_at.desc())).all()
    return {"items": [_task_out(row) for row in rows]}


@router.post("/api/sales-tasks/{task_id}")
def update_task(task_id: UUID, status: str, db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    task = db.get(SalesTask, task_id)
    task.status = status
    return _task_out(task)


@router.get("/api/suppressions")
def list_suppressions(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    rows = db.scalars(select(Suppression).order_by(Suppression.created_at.desc())).all()
    return {"items": [_sup_out(row) for row in rows]}


@router.post("/api/suppressions")
def add_suppression(
    body: SuppressionIn,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(current_settings),
    user: OwnerUser = Depends(require_owner),
):
    from sdr_workspace.services.email_norm import email_key

    contact = db.scalar(select(Contact).where(Contact.email_key == email_key(body.email)))
    if not contact:
        raise HTTPException(404, detail={"code": "not_found"})
    row = suppress(db, contact, scope=body.scope, reason=body.reason, source=user.email, clock=clock, settings=settings)
    return _sup_out(row)


@router.post("/api/conversions")
def add_conversion(
    body: ConversionIn,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    user: OwnerUser = Depends(require_owner),
):
    contact = db.get(Contact, body.contact_id)
    record_conversion(db, contact, body.kind, user.email, clock, body.notes)
    return {"ok": True}


def report_payload(db: Session) -> dict:
    simulated = db.scalar(select(func.count()).select_from(Message).where(Message.status == "simulated")) or 0
    accepted = db.scalar(select(func.count()).select_from(Message).where(Message.status == "accepted")) or 0
    delivered = db.scalar(select(func.count()).select_from(Message).where(Message.status == "delivered")) or 0
    return {
        "contacts": db.scalar(select(func.count()).select_from(Contact)) or 0,
        "active_campaigns": db.scalar(select(func.count()).select_from(Campaign).where(Campaign.status == "active")) or 0,
        "enrollments": db.scalar(select(func.count()).select_from(Enrollment)) or 0,
        "accepted_messages": accepted,
        "simulated_messages": simulated,
        "delivered_messages": delivered,
        "failed_messages": db.scalar(select(func.count()).select_from(Message).where(Message.status == "failed")) or 0,
        "unknown_messages": db.scalar(select(func.count()).select_from(Message).where(Message.status == "unknown")) or 0,
        "open_tasks": db.scalar(select(func.count()).select_from(SalesTask).where(SalesTask.status == "open")) or 0,
        "opt_outs": db.scalar(select(func.count()).select_from(Suppression)) or 0,
        "purchases": db.scalar(select(func.count()).select_from(Conversion).where(Conversion.kind == "purchase")) or 0,
        "replies": db.scalar(select(func.count()).select_from(InboundReply)) or 0,
        "denominators": {
            "messages_attempted": "messages in accepted+simulated+delivered+failed+unknown",
            "attribution_window_days": 30,
            "opens_clicks": "secondary_only_not_in_mvp_metrics",
        },
    }


@router.get("/api/reports")
def reports(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    return report_payload(db)


@router.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db), _: OwnerUser = Depends(require_owner)):
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(200)).all()
    return {
        "items": [
            {
                "id": str(j.id),
                "type": j.job_type,
                "status": j.status,
                "due_at": j.due_at.isoformat(),
                "error": j.last_error,
                "mode": j.delivery_mode,
            }
            for j in rows
        ]
    }


@router.post("/api/demo/tick")
def demo_tick(
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    clock: Clock = Depends(get_clock),
    _: OwnerUser = Depends(require_owner),
):
    summary = dispatch_tick(db, settings, clock)
    jobs = claim_jobs(db, worker_id="manual-tick", lease_seconds=settings.job_lease_seconds, clock=clock, limit=20)
    results = []
    for job in jobs:
        execute_job(db, job, settings, clock)
        results.append({"id": str(job.id), "status": job.status, "error": job.last_error})
    return {"tick": summary, "jobs": results}


@router.post("/api/demo/events")
def demo_event(
    body: dict,
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    clock: Clock = Depends(get_clock),
    _: OwnerUser = Depends(require_owner),
):
    if settings.delivery_mode == "live":
        raise HTTPException(400, detail={"code": "demo_events_disabled_in_live"})
    event = persist_event(
        db,
        provider="demo",
        external_event_id=body.get("id") or uuid4().hex,
        event_type=body.get("type") or "delivered",
        payload=body,
        occurred_at=clock.now(),
        clock=clock,
        delivery_mode=settings.delivery_mode,
    )
    if event:
        apply_event(db, event, settings, clock)
    return {"ok": True, "duplicate": event is None}


@internal.post("/dispatch/tick")
def internal_tick(db: Session = Depends(get_db), settings: Settings = Depends(current_settings), clock: Clock = Depends(get_clock)):
    return dispatch_tick(db, settings, clock)


@internal.post("/prospecting/enroll-ready")
def internal_enroll(db: Session = Depends(get_db), settings: Settings = Depends(current_settings), clock: Clock = Depends(get_clock)):
    return enroll_ready_prospects(db, settings, clock)


@internal.post("/inbox/events")
def inbox(body: InboxEvent, db: Session = Depends(get_db), settings: Settings = Depends(current_settings), clock: Clock = Depends(get_clock)):
    reply = ingest_reply(
        db,
        from_email=body.from_email,
        subject=body.subject,
        body=body.newest_content,
        quoted=body.quoted_content,
        thread_id=body.thread_id,
        message_id_header=body.message_id_header,
        settings=settings,
        clock=clock,
    )
    return {"id": str(reply.id), "intent": reply.intent, "paused": reply.paused_before_classify}


@internal.get("/reports/daily")
def daily_report(db: Session = Depends(get_db)):
    return report_payload(db)


@webhooks.post("/webhooks/sendgrid")
async def sendgrid_webhook(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(current_settings), clock: Clock = Depends(get_clock)):
    raw = await request.body()
    signature = request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
    timestamp = request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")
    if settings.sendgrid_webhook_public_key:
        if not verify_sendgrid_signature(settings.sendgrid_webhook_public_key, raw, signature, timestamp, settings.webhook_max_skew_seconds, clock):
            raise HTTPException(401, detail={"code": "invalid_signature"})
    elif settings.delivery_mode == "live":
        raise HTTPException(401, detail={"code": "unsigned_rejected"})
    events = json.loads(raw.decode() or "[]")
    stored = 0
    for item in events:
        event = persist_event(
            db,
            provider="sendgrid",
            external_event_id=str(item.get("sg_event_id") or item.get("sg_message_id") or uuid4()),
            event_type=item.get("event") or "unknown",
            payload=item,
            occurred_at=datetime.fromtimestamp(item.get("timestamp") or clock.now().timestamp(), tz=timezone.utc),
            clock=clock,
            delivery_mode=settings.delivery_mode,
        )
        if event:
            apply_event(db, event, settings, clock)
            stored += 1
    return {"stored": stored}


@webhooks.post("/webhooks/smartlead")
async def smartlead_webhook(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(current_settings), clock: Clock = Depends(get_clock)):
    if request.headers.get("X-Internal-Key") != settings.internal_api_key and settings.delivery_mode == "live":
        raise HTTPException(401, detail={"code": "unauthenticated_webhook"})
    payload = await request.json()
    event = persist_event(
        db,
        provider="smartlead",
        external_event_id=str(payload.get("webhook_id") or payload.get("id") or uuid4()),
        event_type=str(payload.get("event_type") or payload.get("type") or "unknown"),
        payload=payload,
        occurred_at=clock.now(),
        clock=clock,
        delivery_mode=settings.delivery_mode,
    )
    if event:
        apply_event(db, event, settings, clock)
    return {"ok": True}


@public.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, settings: Settings = Depends(current_settings)):
    try:
        parse_unsubscribe_token(settings, token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail={"code": "invalid_token"}) from exc
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Email preferences</title></head>
<body>
<h1>Email preferences</h1>
<p>This page view does not change your preferences. Submit the form to opt out of marketing.</p>
<form method="post" action="/unsubscribe/{escape(token)}">
<button type="submit">Unsubscribe from all marketing</button>
</form>
</body></html>"""


@public.post("/unsubscribe/{token}")
def unsubscribe_action(
    token: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
    clock: Clock = Depends(get_clock),
):
    contact_id = parse_unsubscribe_token(settings, token)
    contact = db.get(Contact, UUID(contact_id))
    if contact:
        suppress(db, contact, scope="marketing", reason="one_click", source="preferences", clock=clock, settings=settings)
    return HTMLResponse("<p>You are unsubscribed from marketing email.</p>")


@public.get("/health")
def health():
    return {"status": "ok"}


@public.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(select(func.now()))
    return {"status": "ready"}


def _contact_out(db: Session, contact: Contact) -> dict:
    blocked = db.scalar(select(Suppression).where(Suppression.email_key == contact.email_key).limit(1))
    return {
        "id": str(contact.id),
        "email": contact.original_email,
        "name": f"{contact.first_name} {contact.last_name}".strip(),
        "company": contact.company,
        "role": contact.role,
        "segment": contact.segment,
        "country": contact.country,
        "timezone": contact.timezone,
        "source": contact.source,
        "lifecycle_status": contact.lifecycle_status,
        "audience_reviewed": contact.audience_reviewed,
        "suppressed": bool(blocked),
    }


def _campaign_out(db: Session, campaign: Campaign) -> dict:
    steps = db.scalars(select(CampaignStep).where(CampaignStep.campaign_id == campaign.id).order_by(CampaignStep.step_number)).all()
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "campaign_type": campaign.campaign_type,
        "audience_segment": campaign.audience_segment,
        "provider": campaign.provider,
        "status": campaign.status,
        "content_revision": campaign.content_revision,
        "approved_revision": campaign.approved_revision,
        "delivery_mode": campaign.delivery_mode,
        "objective": campaign.objective,
        "offer_id": str(campaign.offer_id) if campaign.offer_id else None,
        "steps": [
            {"id": str(s.id), "step_number": s.step_number, "delay_days": s.delay_days, "template": s.template, "cta": s.call_to_action}
            for s in steps
        ],
    }


def _enrollment_out(row: Enrollment) -> dict:
    return {
        "id": str(row.id),
        "contact_id": str(row.contact_id),
        "campaign_id": str(row.campaign_id),
        "status": row.status,
        "current_step": row.current_step,
        "next_send_at": row.next_send_at.isoformat() if row.next_send_at else None,
        "stop_reason": row.stop_reason,
        "delivery_mode": row.delivery_mode,
        "provider_sync_status": row.provider_sync_status,
        "external_lead_id": row.external_lead_id,
    }


def _offer_out(row: Offer) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "product": row.product,
        "price_text": row.price_text,
        "terms": row.terms,
        "destination_url": row.destination_url,
        "status": row.status,
        "code": row.code,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _knowledge_out(row: KnowledgeSource) -> dict:
    return {
        "id": str(row.id),
        "title": row.title,
        "body": row.body,
        "evidence_id": row.evidence_id,
        "status": row.status,
        "source_ref": row.source_ref,
    }


def _message_out(row: Message) -> dict:
    return {
        "id": str(row.id),
        "contact_id": str(row.contact_id),
        "enrollment_id": str(row.enrollment_id),
        "step_number": row.step_number,
        "status": row.status,
        "subject": row.final_subject or row.draft_subject,
        "body": row.final_body or row.draft_body,
        "evidence_ids": row.evidence_ids,
        "needs_review": row.needs_review,
        "review_reasons": row.review_reasons,
        "mode": row.delivery_mode,
        "provider_message_id": row.provider_message_id,
        "correlation_id": row.correlation_id,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
    }


def _task_out(row: SalesTask) -> dict:
    return {
        "id": str(row.id),
        "contact_id": str(row.contact_id),
        "status": row.status,
        "intent": row.intent,
        "summary": row.summary,
        "suggested_reply": row.suggested_reply,
    }


def _sup_out(row: Suppression) -> dict:
    return {
        "id": str(row.id),
        "email_key": row.email_key,
        "scope": row.scope,
        "reason": row.reason,
        "source": row.source,
        "provider_sync_status": row.provider_sync_status,
    }

from __future__ import annotations

from collections.abc import Callable, Generator
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock, SystemClock
from sdr_workspace.config import Settings, get_settings
from sdr_workspace.db import SessionLocal
from sdr_workspace.models import OwnerUser
from sdr_workspace.services.security import session_serializer, verify_password
from sqlalchemy import select


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_clock() -> Clock:
    return SystemClock()


def current_settings() -> Settings:
    return get_settings()


def require_owner(
    sdr_session: str | None = Cookie(default=None, alias="sdr_session"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
) -> OwnerUser:
    if not sdr_session:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    try:
        data = session_serializer(settings).loads(sdr_session, max_age=60 * 60 * 24 * 7)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail={"code": "unauthorized"}) from exc
    user = db.scalar(select(OwnerUser).where(OwnerUser.email == data.get("email")))
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    return user


def require_internal(
    x_internal_key: str | None = Header(default=None),
    settings: Settings = Depends(current_settings),
) -> None:
    if x_internal_key != settings.internal_api_key:
        raise HTTPException(status_code=401, detail={"code": "unauthorized_internal"})

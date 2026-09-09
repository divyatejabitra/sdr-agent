from __future__ import annotations

from itsdangerous import BadSignature, URLSafeTimedSerializer

from sdr_workspace.config import Settings


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.unsubscribe_secret, salt="unsubscribe")


def make_unsubscribe_token(settings: Settings, contact_id) -> str:
    return _serializer(settings).dumps({"contact_id": str(contact_id)})


def parse_unsubscribe_token(settings: Settings, token: str) -> str:
    data = _serializer(settings).loads(token, max_age=60 * 60 * 24 * 365)
    return data["contact_id"]


def unsubscribe_url(settings: Settings, contact_id) -> str:
    token = make_unsubscribe_token(settings, contact_id)
    return f"{settings.public_base_url}/unsubscribe/{token}"

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from sdr_workspace.clock import Clock


def _load_public_key(key_material: str):
    raw = key_material.strip()
    if "BEGIN PUBLIC KEY" not in raw:
        raw = "-----BEGIN PUBLIC KEY-----\n" + raw + "\n-----END PUBLIC KEY-----"
    return serialization.load_pem_public_key(raw.encode())


def verify_sendgrid_signature(
    public_key: str,
    payload: bytes,
    signature_b64: str,
    timestamp: str,
    max_skew_seconds: int,
    clock: Clock,
) -> bool:
    if not public_key or not signature_b64 or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    now = int(clock.now().timestamp())
    if abs(now - ts) > max_skew_seconds:
        return False
    try:
        key = _load_public_key(public_key)
        signature = base64.b64decode(signature_b64)
        hashed = hashlib.sha256(timestamp.encode() + payload).digest()
        key.verify(signature, hashed, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return True
    except Exception:
        try:
            key = _load_public_key(public_key)
            signature = base64.b64decode(signature_b64)
            key.verify(signature, timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False

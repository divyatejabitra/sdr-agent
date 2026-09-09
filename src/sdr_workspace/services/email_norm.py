from __future__ import annotations

import re

_PLUS = re.compile(r"\+[^@]+")


def email_key(original: str) -> str:
    """Case-fold only. Plus tags and Gmail dots are preserved on purpose."""
    value = original.strip().lower()
    if "@" not in value:
        return value
    local, domain = value.split("@", 1)
    return f"{local}@{domain}"


def split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]

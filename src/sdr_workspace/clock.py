from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    def __init__(self, instant: datetime) -> None:
        self._now = instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def set(self, instant: datetime) -> None:
        self._now = instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + __import__("datetime").timedelta(**kwargs)
        return self._now

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sdr_workspace.clock import Clock


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def in_sending_window(when: datetime, tz_name: str, start: str, end: str) -> bool:
    local = when.astimezone(ZoneInfo(tz_name))
    if local.weekday() >= 5:
        return False
    start_t = parse_hhmm(start)
    end_t = parse_hhmm(end)
    current = local.timetz().replace(tzinfo=None)
    return start_t <= current < end_t


def next_in_window(when: datetime, tz_name: str, start: str, end: str) -> datetime:
    tz = ZoneInfo(tz_name)
    local = when.astimezone(tz)
    start_t = parse_hhmm(start)
    candidate = local.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
    if local.weekday() < 5 and in_sending_window(when, tz_name, start, end):
        return when
    if local.timetz().replace(tzinfo=None) >= parse_hhmm(end) or local.weekday() >= 5:
        candidate = (local + timedelta(days=1)).replace(
            hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0
        )
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
        candidate = candidate.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
    if candidate < local:
        candidate = (local + timedelta(days=1)).replace(
            hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0
        )
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate.astimezone(ZoneInfo("UTC"))


def add_delay_in_window(
    clock: Clock,
    tz_name: str,
    start: str,
    end: str,
    delay_days: int,
    from_time: datetime | None = None,
) -> datetime:
    base = from_time or clock.now()
    target = base + timedelta(days=delay_days)
    return next_in_window(target, tz_name, start, end)

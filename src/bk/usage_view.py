from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

from .timeparse import parse_iso


TrendRow = tuple[date, tuple[float, float]]


def build_activity_trends(
    records: Iterable[dict],
    end: datetime,
    *,
    week_count: int = 4,
) -> tuple[list[TrendRow], list[TrendRow]]:
    """Aggregate sampled active/reserved seconds into local day and week rows."""
    daily: dict[date, list[float]] = {}
    weekly: dict[date, list[float]] = {}
    for record in records:
        try:
            local_start = parse_iso(str(record["window_start"])).astimezone()
        except (KeyError, TypeError, ValueError):
            continue
        active = max(0.0, float(record.get("active_observed_seconds", 0)))
        reserved = (
            max(0.0, float(record.get("observed_seconds", 0)))
            if str(record.get("status", "")) == "ok"
            else 0.0
        )
        day = local_start.date()
        week = day - timedelta(days=day.weekday())
        for groups, key in ((daily, day), (weekly, week)):
            values = groups.setdefault(key, [0.0, 0.0])
            values[0] += active
            values[1] += reserved

    today = end.astimezone().date()
    current_week = today - timedelta(days=today.weekday())
    daily_rows = [
        _trend_row(day, daily.get(day))
        for day in (today - timedelta(days=offset) for offset in range(6, -1, -1))
    ]
    weekly_rows = [
        _trend_row(week, weekly.get(week))
        for week in (
            current_week - timedelta(days=7 * offset)
            for offset in range(max(0, week_count - 1), -1, -1)
        )
    ]
    return daily_rows, weekly_rows


def activity_bar_cells(
    active_hours: float,
    reserved_hours: float,
    peak_reserved_hours: float,
    width: int,
) -> tuple[int, int]:
    width = max(1, width)
    if peak_reserved_hours <= 0 or reserved_hours <= 0:
        return 0, 0
    reserved_cells = max(1, round(reserved_hours / peak_reserved_hours * width))
    active_cells = min(reserved_cells, round(active_hours / peak_reserved_hours * width))
    return active_cells, reserved_cells - active_cells


def format_usage_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return "".join(parts) or "0m"


def format_usage_memory(memory_mb: int) -> str:
    return "-" if memory_mb <= 0 else f"{memory_mb / 1024:.1f}G"


def _trend_row(period: date, values: Sequence[float] | None) -> TrendRow:
    active, reserved = values or (0.0, 0.0)
    return period, (active, reserved)

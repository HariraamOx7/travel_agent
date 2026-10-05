"""Conservative reader for simple OSM weekly opening hours.

Unsupported expressions return None, meaning unknown. They are never
mistaken for a confirmed closure.
"""
from __future__ import annotations

import re
from datetime import date

_DAYS = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3,
         "Fr": 4, "Sa": 5, "Su": 6}
_RULE = re.compile(r"^(?:(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?\s+)?(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _windows(hours: str | None, day: date):
    if not hours:
        return None
    if hours.strip() == "24/7":
        return [(0, 1440)]
    windows = []
    for part in hours.split(";"):
        match = _RULE.fullmatch(part.strip())
        if not match:
            return None
        first, last, opening, closing = match.groups()
        if first:
            start = _DAYS[first]
            end = _DAYS[last] if last else start
            days = set(range(start, end + 1)) if start <= end else (
                set(range(start, 7)) | set(range(0, end + 1)))
            if day.weekday() not in days:
                continue
        windows.append((_minutes(opening), _minutes(closing)))
    return windows


def earliest_start(hours: str | None, day: date, arrive_min: int,
                   visit_min: int) -> int | None:
    windows = _windows(hours, day)
    if windows is None:
        return arrive_min
    starts = [max(begin, arrive_min) for begin, end in windows
              if max(begin, arrive_min) + visit_min <= end]
    return min(starts) if starts else None


def visit_fits(hours: str | None, day: date, arrive_min: int,
               visit_min: int) -> bool | None:
    windows = _windows(hours, day)
    if windows is None:
        return None
    return any(begin <= arrive_min and arrive_min + visit_min <= end
               for begin, end in windows)

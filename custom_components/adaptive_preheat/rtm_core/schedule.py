"""Next temperature rise of a weekly schedule (pure, stdlib only).

The input is the response of Home Assistant's ``schedule.get_schedule`` service: weekday
names (``monday`` ... ``sunday``) mapping to lists of blocks ``{"from", "to", "data"}``
whose ``data`` holds the target temperature under a configurable key. Times are wall-clock
times of the schedule's own time zone, which is the timezone of the ``now`` passed in.

A *rise* is the start of the first block in the horizon whose target is more than
``MIN_RISE_K`` above the target in force just before it:

* inside a block, the baseline is that block's target; a block that starts exactly when the
  previous one ends is compared with the previous block;
* after a gap (or when outside any block) the schedule is off and the baseline is the
  ``off_target`` (the target the zone has now when outside a block, unknown otherwise: any
  block is then a rise).

Malformed blocks, unknown weekdays and blocks without the key are skipped. A block ending
at ``24:00:00`` (or ``00:00:00``, or ``time.max``) ends at midnight.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, cast

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MIN_RISE_K = 0.5
DEFAULT_HORIZON = timedelta(hours=24)
DAY_SECONDS = 86400


@dataclass(frozen=True, slots=True)
class Block:
    """One schedule block on one weekday (seconds since local midnight)."""

    weekday: int  # Monday = 0
    start_s: int
    end_s: int  # DAY_SECONDS for "until midnight"
    target_c: float


@dataclass(frozen=True, slots=True)
class Rise:
    """The next block that raises the target."""

    start: datetime
    target_c: float


def _seconds(value: Any) -> int | None:
    """Seconds since midnight of ``"HH:MM[:SS]"`` or a ``time`` (None when invalid)."""
    if isinstance(value, time):
        if value == time.max:
            return DAY_SECONDS
        return value.hour * 3600 + value.minute * 60 + value.second
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    hours, minutes = int(parts[0]), int(parts[1])
    seconds = int(parts[2]) if len(parts) == 3 else 0
    total = hours * 3600 + minutes * 60 + seconds
    if minutes > 59 or seconds > 59 or total > DAY_SECONDS:
        return None
    return total


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_blocks(raw: Any, key: str) -> list[Block]:
    """Blocks of one schedule entity's ``get_schedule`` result (invalid ones skipped)."""
    blocks: list[Block] = []
    if not isinstance(raw, Mapping):
        return blocks
    weekdays = cast("Mapping[str, Any]", raw)
    for index, name in enumerate(WEEKDAYS):
        day = weekdays.get(name)
        if not isinstance(day, list):
            continue
        for item in cast("list[Any]", day):
            if not isinstance(item, Mapping):
                continue
            fields = cast("Mapping[str, Any]", item)
            start = _seconds(fields.get("from"))
            end = _seconds(fields.get("to"))
            data = fields.get("data")
            if start is None or end is None or not isinstance(data, Mapping):
                continue
            target = _finite(cast("Mapping[str, Any]", data).get(key))
            if target is None:
                continue
            if end == 0:
                end = DAY_SECONDS
            if end <= start or start >= DAY_SECONDS:
                continue
            blocks.append(Block(index, start, end, target))
    return blocks


def _at(day: date, seconds: int, now: datetime) -> datetime:
    """Wall-clock ``seconds`` after midnight of ``day`` in the zone of ``now``."""
    base = datetime.combine(day, time(), tzinfo=now.tzinfo)
    if seconds >= DAY_SECONDS:
        base = datetime.combine(day + timedelta(days=1), time(), tzinfo=now.tzinfo)
        seconds -= DAY_SECONDS
    return base.replace(hour=seconds // 3600, minute=seconds % 3600 // 60, second=seconds % 60)


def _occurrences(
    blocks: list[Block], now: datetime, horizon: timedelta
) -> list[tuple[datetime, datetime, float]]:
    """(start, end, target) of every block from yesterday until the horizon, sorted."""
    found: list[tuple[datetime, datetime, float]] = []
    first = now.date() - timedelta(days=1)
    days = horizon.days + 3
    for offset in range(days):
        day = first + timedelta(days=offset)
        found.extend(
            (_at(day, b.start_s, now), _at(day, b.end_s, now), b.target_c)
            for b in blocks
            if b.weekday == day.weekday()
        )
    found.sort(key=lambda occ: occ[0])
    return found


def next_rise(
    blocks: list[Block],
    now: datetime,
    current_target: float | None,
    horizon: timedelta = DEFAULT_HORIZON,
) -> Rise | None:
    """First future block start within ``horizon`` that raises the target, else None.

    ``now`` must be timezone-aware in the schedule's time zone (local time).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not blocks:
        return None
    limit = now + horizon
    off_target = current_target  # target of the zone while the schedule is off
    baseline: float | None = None
    previous_end: datetime | None = None
    for start, end, target in _occurrences(blocks, now, horizon):
        if start <= now:
            if end > now:  # inside this block: its own target is the baseline
                baseline, previous_end, off_target = target, end, None
            continue
        if start > limit:
            break
        contiguous = previous_end is not None and start == previous_end
        reference = baseline if contiguous else off_target
        if reference is None or target > reference + MIN_RISE_K:
            return Rise(start, target)
        baseline, previous_end = target, end
    return None

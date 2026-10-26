"""Parse a JSON-lines history file into :class:`Sample` rows (pure standard library).

One JSON object per line::

    {"ts": "2026-01-05T07:00:00+00:00", "indoor_c": 18.4, "outdoor_c": 3.1, "effort": 0.0}

``ts`` is required and must carry a UTC offset. ``indoor_c``, ``outdoor_c`` and ``effort``
may be ``null`` or absent (unknown). ``effort`` is the heating effort in [0, 1]. Blank
lines are skipped. Rows may be in any order (they are sorted; a repeated timestamp keeps
the last row). Any invalid line rejects the whole file: a partial import would silently
teach the model from a truncated series.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from .base import Sample

MAX_ROWS = 2_000_000
MAX_REPORTED_ERRORS = 5
TEMPERATURE_RANGE_C = (-60.0, 80.0)


class HistoryImportError(ValueError):
    """The file cannot be imported (the message names the first offending lines)."""


@dataclass(frozen=True)
class ParsedHistory:
    """Samples sorted by time plus facts about the file."""

    samples: list[Sample]
    lines: int
    reordered: bool
    duplicates: int


def _number(value: Any, name: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number or null")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} {value} is outside {low:g}..{high:g}")
    return number


def parse_line(text: str) -> Sample:
    """One line to a Sample (raises ``ValueError`` with the reason)."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"not valid JSON ({err.msg})") from err
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object")
    row: dict[str, Any] = obj  # pyright: ignore[reportUnknownVariableType]
    raw_ts = row.get("ts")
    if not isinstance(raw_ts, str):
        raise ValueError("missing ts (ISO 8601 string)")
    try:
        ts = datetime.fromisoformat(raw_ts)
    except ValueError as err:
        raise ValueError(f"ts {raw_ts!r} is not ISO 8601") from err
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"ts {raw_ts!r} has no UTC offset (use e.g. +00:00 or Z)")
    low, high = TEMPERATURE_RANGE_C
    return Sample(
        ts=ts,
        indoor_c=_number(row.get("indoor_c"), "indoor_c", low, high),
        outdoor_c=_number(row.get("outdoor_c"), "outdoor_c", low, high),
        effort=_number(row.get("effort"), "effort", 0.0, 1.0),
    )


def parse_history(lines: Iterable[str]) -> ParsedHistory:
    """Parse every line; raise :class:`HistoryImportError` listing the first bad ones."""
    samples: list[Sample] = []
    errors: list[str] = []
    bad = 0
    total = 0
    for number, text in enumerate(lines, start=1):
        if not text.strip():
            continue
        total += 1
        if total > MAX_ROWS:
            raise HistoryImportError(f"more than {MAX_ROWS} rows: split the file")
        try:
            samples.append(parse_line(text))
        except ValueError as err:
            bad += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"line {number}: {err}")
    if bad:
        more = f" (and {bad - len(errors)} more)" if bad > len(errors) else ""
        raise HistoryImportError("; ".join(errors) + more)
    if not samples:
        raise HistoryImportError("the file has no rows")
    reordered = any(a.ts > b.ts for a, b in pairwise(samples))
    if reordered:
        samples.sort(key=lambda s: s.ts)  # stable: a repeated timestamp keeps file order
    unique: dict[datetime, Sample] = {s.ts: s for s in samples}
    duplicates = len(samples) - len(unique)
    if duplicates:
        samples = list(unique.values())
    return ParsedHistory(samples, total, reordered, duplicates)

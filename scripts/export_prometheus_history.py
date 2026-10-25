#!/usr/bin/env python3
"""Export room history from a Prometheus-compatible HTTP API as JSON lines.

The output is the file format of the ``room_thermal_model.import_history`` service, one row
per step: ``{"ts": ..., "indoor_c": ..., "outdoor_c": ..., "effort": ...}``. Standard
library only. The range is fetched in chunks so every request stays below the 11000 point
limit of Prometheus and Mimir. Each expression must return a single series.

Example (see the README for a worked one)::

    python scripts/export_prometheus_history.py http://prometheus.example:9009/prometheus \\
        --start 2026-01-01 --end 2026-03-01 --step 5m --output living_room.jsonl \\
        --indoor 'temperature_celsius{entity="sensor.living_room_temperature"}' \\
        --outdoor 'temperature_celsius{entity="sensor.outdoor_temperature"}' \\
        --effort 'valve_percent{entity="sensor.living_room_valve"} / 100'
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

MAX_POINTS = 10000  # per request, under the 11000 limit of Prometheus
_DURATION = re.compile(r"(\d+(?:\.\d+)?)([smhdw])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class ExportError(Exception):
    """A problem to report to the user (not a bug)."""


def parse_duration(text: str) -> float:
    """Seconds of ``5m``, ``90s``, ``1h30m`` or a plain number of seconds."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    parts = _DURATION.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ExportError(f"cannot read duration {text!r} (use e.g. 5m, 1h30m or seconds)")
    return sum(float(n) * _UNITS[u] for n, u in parts)


def parse_time(text: str) -> float:
    """Epoch seconds of an ISO 8601 date/time (UTC when no offset) or epoch seconds."""
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as err:
        raise ExportError(f"cannot read time {text!r} (use ISO 8601 or epoch seconds)") from err
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def chunk_ranges(
    start: float, end: float, step: float, max_points: int = MAX_POINTS
) -> Iterator[tuple[float, float]]:
    """Consecutive ``(chunk_start, chunk_end)`` ranges of at most ``max_points`` points.

    Chunks do not overlap: the next one starts one step after the last point of the
    previous one, so every grid point is requested exactly once.
    """
    if step <= 0:
        raise ExportError("step must be positive")
    if end < start:
        raise ExportError("end is before start")
    span = (max_points - 1) * step
    cursor = start
    while cursor <= end:
        last = min(cursor + span, end)
        yield cursor, last
        cursor = cursor + span + step


def parse_matrix(payload: Any, expression: str) -> dict[float, float]:
    """``{timestamp: value}`` from a ``query_range`` response (a single series only)."""
    if not isinstance(payload, dict) or payload.get("status") != "success":  # pyright: ignore[reportUnnecessaryIsInstance]
        message = payload.get("error") if isinstance(payload, dict) else payload
        raise ExportError(f"query failed for {expression!r}: {message}")
    result = payload.get("data", {}).get("result", [])
    if len(result) > 1:
        labels = [json.dumps(r.get("metric", {}), sort_keys=True) for r in result[:3]]
        raise ExportError(
            f"{expression!r} returned {len(result)} series, expected one; narrow the selector "
            f"(first: {', '.join(labels)})"
        )
    points: dict[float, float] = {}
    for ts, raw in result[0].get("values", []) if result else []:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):  # "NaN" and "+Inf" are Prometheus gaps
            points[float(ts)] = value
    return points


def build_rows(
    series: dict[str, dict[float, float]], start: float, end: float, step: float
) -> Iterator[dict[str, Any]]:
    """Rows on the step grid where at least one series has a value.

    ``series`` maps ``indoor_c`` / ``outdoor_c`` / ``effort`` to ``{timestamp: value}``.
    Temperatures are rounded to 0.01 and effort is clamped to [0, 1].
    """
    count = int((end - start) // step) + 1
    for i in range(count):
        t = start + i * step
        row: dict[str, Any] = {"ts": datetime.fromtimestamp(t, UTC).isoformat()}
        found = False
        for name in ("indoor_c", "outdoor_c", "effort"):
            value = series.get(name, {}).get(t)
            if value is None:
                row[name] = None
                continue
            found = True
            row[name] = round(min(1.0, max(0.0, value)), 3) if name == "effort" else round(value, 2)
        if found:
            yield row


def query_range(
    base_url: str,
    expression: str,
    start: float,
    end: float,
    step: float,
    headers: Sequence[tuple[str, str]] = (),
    timeout: float = 60.0,
) -> dict[float, float]:
    """One ``query_range`` request (POST, so long expressions fit)."""
    url = base_url.rstrip("/") + "/api/v1/query_range"
    body = urllib.parse.urlencode(
        {"query": expression, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": f"{step:g}"}
    ).encode()
    request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310 (http(s) only, checked in main)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    for name, value in headers:
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise ExportError(f"HTTP {err.code} from {url}: {detail}") from err
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise ExportError(f"cannot query {url}: {err}") from err
    return parse_matrix(payload, expression)


def parse_header(text: str) -> tuple[str, str]:
    name, sep, value = text.partition(":")
    if not sep or not name.strip():
        raise ExportError(f"header {text!r} must look like 'Name: value'")
    return name.strip(), value.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("url", help="API base URL, the part before /api/v1 (include any prefix)")
    parser.add_argument(
        "--start", required=True, help="ISO 8601 (UTC if no offset) or epoch seconds"
    )
    parser.add_argument("--end", required=True, help="same format as --start")
    parser.add_argument("--step", default="5m", help="resolution, default 5m (the model grid)")
    parser.add_argument("--indoor", required=True, help="PromQL for the room temperature in C")
    parser.add_argument("--outdoor", help="PromQL for the outdoor temperature in C")
    parser.add_argument(
        "--effort", help="PromQL for the heating effort 0..1 (gate it by the boiler)"
    )
    parser.add_argument("--output", required=True, help="JSON-lines file to write")
    parser.add_argument(
        "--header", action="append", default=[], help="extra HTTP header 'Name: value' (repeatable)"
    )
    return parser


def export(args: argparse.Namespace) -> int:
    if not args.url.startswith(("http://", "https://")):
        raise ExportError("url must start with http:// or https://")
    step = parse_duration(args.step)
    start = parse_time(args.start)
    end = parse_time(args.end)
    start = math.ceil(start / step) * step  # align to the step so rows land on the grid
    headers = [parse_header(h) for h in args.header]
    queries = {"indoor_c": args.indoor, "outdoor_c": args.outdoor, "effort": args.effort}
    queries = {name: q for name, q in queries.items() if q}
    series: dict[str, dict[float, float]] = {name: {} for name in queries}
    chunks = list(chunk_ranges(start, end, step))
    for n, (c0, c1) in enumerate(chunks, start=1):
        for name, expression in queries.items():
            series[name].update(query_range(args.url, expression, c0, c1, step, headers))
        print(f"chunk {n}/{len(chunks)} done", file=sys.stderr)
    rows = 0
    with open(args.output, "w", encoding="utf-8") as handle:
        for row in build_rows(series, start, end, step):
            handle.write(json.dumps(row) + "\n")
            rows += 1
    if not rows:
        raise ExportError("no data in that range: check the expressions and the dates")
    span = timedelta(seconds=end - start)
    print(f"wrote {rows} rows over {span} to {args.output}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return export(args)
    except ExportError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

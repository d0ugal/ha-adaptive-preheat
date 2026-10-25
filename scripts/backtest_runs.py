#!/usr/bin/env python3
"""Back-test the preheat prediction and its self-calibration on recorded history.

Standalone: imports only ``rtm_core`` (no Home Assistant). The input is a JSON-lines file,
one object per row (usually every 5 minutes)::

    {"ts": "2026-01-05T06:00:00+00:00", "indoor_c": 17.9, "outdoor_c": 4.2, "effort": 0.8}

``effort`` is the heating demand in 0..1 (already gated by the zone being on), any of the
temperatures and the effort may be null.

History has no thermostat targets, so runs are reconstructed with a documented PROXY:

* a heating episode starts at the first row with ``effort >= --effort`` and lasts while
  such rows keep coming (a pause of at most ``--max-pause`` minutes is bridged);
* it counts only if the room rose by at least ``--min-rise`` K from the start;
* the target is the highest indoor temperature seen during the episode.

The proxy is generous to the model (the target is known to be reachable and was in fact
reached) but it is not what a user asked for: treat the numbers as a relative baseline.

Each episode is fed through the real ``RunScorer`` with the same rules as the live
integration (open on effort > 0 with the room at least 0.5 K below the target and an
estimate, closed within 0.2 K of the target, clean when heating >= 80 % of the time). The
estimate comes from a model fitted ONLY on data before the run (refitted at most every
``--refit-days``, on the last ``--history-days``). Calibration is evaluated
chronologically: the factor applied to a run is the one the scorer had learnt from the
runs before it, exactly as in the integration.

    python scripts/backtest_runs.py history/study.jsonl [more.jsonl ...] [--json]
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "room_thermal_model")
)

from rtm_core import ModelConfig, RCThermalModel, RunResult, RunScorer, Sample
from rtm_core.calibration import MIN_RUNS

EFFORT_HIGH = 0.5
MAX_PAUSE_MINUTES = 15.0
MIN_RISE_K = 1.0
GRID_MINUTES = 5.0
MAX_AGE_MINUTES = 15.0  # indoor and effort carried forward this long (as the model does)
OUTDOOR_MAX_AGE_MINUTES = 180.0
REFIT_DAYS = 7.0
HISTORY_DAYS = 30.0


@dataclass(frozen=True)
class Episode:
    """A reconstructed heating episode: rows ``start..end`` (inclusive) and its proxy target."""

    start: int
    end: int
    target_c: float


# ------------------------------------------------------------------ pure parts


def parse_row(line: str) -> Sample:
    """One JSON line to a ``Sample`` (missing or null values become None)."""
    raw = json.loads(line)

    def num(key: str) -> float | None:
        value = raw.get(key)
        return None if value is None else float(value)

    return Sample(
        ts=datetime.fromisoformat(raw["ts"]),
        indoor_c=num("indoor_c"),
        outdoor_c=num("outdoor_c"),
        effort=num("effort"),
    )


def regrid(
    rows: Sequence[Sample],
    step_minutes: float = GRID_MINUTES,
    max_age_minutes: float = MAX_AGE_MINUTES,
    outdoor_max_age_minutes: float = OUTDOOR_MAX_AGE_MINUTES,
) -> list[Sample]:
    """Merge sparse, unaligned observations onto a regular grid (last value carried forward).

    Some exports hold the indoor temperature and the effort on different rows; the grid
    value of each field is the latest observation at or before the grid time, no older than
    ``max_age_minutes`` (``outdoor_max_age_minutes`` for the slowly moving outdoor figure).
    Grid times with no value at all are dropped.
    """
    if not rows:
        return []
    ordered = sorted(rows, key=lambda s: s.ts)
    step = timedelta(minutes=step_minutes)
    epoch = datetime(1970, 1, 1, tzinfo=ordered[0].ts.tzinfo)
    first = epoch + -((epoch - ordered[0].ts) // step) * step
    out: list[Sample] = []
    last: dict[str, tuple[datetime, float]] = {}
    ages = {"indoor_c": max_age_minutes, "effort": max_age_minutes}
    ages["outdoor_c"] = outdoor_max_age_minutes
    i, n = 0, len(ordered)
    t = first
    end = ordered[-1].ts
    while t <= end:
        while i < n and ordered[i].ts <= t:
            row = ordered[i]
            for key, value in (
                ("indoor_c", row.indoor_c),
                ("outdoor_c", row.outdoor_c),
                ("effort", row.effort),
            ):
                if value is not None:
                    last[key] = (row.ts, value)
            i += 1
        fresh: dict[str, float | None] = {}
        for key, age in ages.items():
            seen = last.get(key)
            fresh[key] = seen[1] if seen and (t - seen[0]).total_seconds() / 60.0 <= age else None
        if any(v is not None for v in fresh.values()):
            out.append(Sample(t, fresh["indoor_c"], fresh["outdoor_c"], fresh["effort"]))
        t += step
    return out


def load_rows(path: Path) -> list[Sample]:
    """Read a history file and put it on a regular 5 minute grid."""
    with path.open() as handle:
        rows = [parse_row(line) for line in handle if line.strip()]
    return regrid(rows)


def find_episodes(
    rows: Sequence[Sample],
    effort_high: float = EFFORT_HIGH,
    max_pause_minutes: float = MAX_PAUSE_MINUTES,
    min_rise_k: float = MIN_RISE_K,
) -> list[Episode]:
    """Heating episodes where the room rose by at least ``min_rise_k``, with proxy targets."""
    episodes: list[Episode] = []
    start: int | None = None
    last_high = 0
    for i, row in enumerate(rows):
        high = row.effort is not None and row.effort >= effort_high and row.indoor_c is not None
        if high:
            if start is None:
                start = i
            elif (row.ts - rows[last_high].ts).total_seconds() / 60.0 > max_pause_minutes:
                _close_episode(rows, start, last_high, min_rise_k, episodes)
                start = i
            last_high = i
        elif start is not None and (row.ts - rows[last_high].ts).total_seconds() / 60.0 > (
            max_pause_minutes
        ):
            _close_episode(rows, start, last_high, min_rise_k, episodes)
            start = None
    if start is not None:
        _close_episode(rows, start, last_high, min_rise_k, episodes)
    return episodes


def _close_episode(
    rows: Sequence[Sample], start: int, end: int, min_rise_k: float, out: list[Episode]
) -> None:
    temps = [r.indoor_c for r in rows[start : end + 1] if r.indoor_c is not None]
    first = rows[start].indoor_c
    if not temps or first is None:
        return
    peak = max(temps)
    if peak - first >= min_rise_k:
        out.append(Episode(start, end, peak))


Predict = Callable[[Sample], float | None]


def score_episode(
    scorer: RunScorer, rows: Sequence[Sample], episode: Episode, predict: Predict
) -> RunResult | None:
    """Replay one episode through the scorer. ``predict`` is asked only until a run opens."""
    for row in rows[episode.start : episode.end + 1]:
        raw: float | None = None
        published: float | None = None
        heating = None if row.effort is None else row.effort > 0
        if scorer.open_run is None and heating:
            raw = predict(row)
            if raw is not None:
                published = raw * scorer.calibration().factor
        result = scorer.observe(row.ts, row.indoor_c, episode.target_c, raw, heating, published)
        if result is not None:
            return result
    # The proxy target is the episode peak, so a run that opened has closed by now; this
    # only covers a run still open at the end (not scored, as in the integration).
    if scorer.open_run is not None:
        scorer.observe(rows[episode.end].ts, None, episode.target_c, None, None)
        return scorer.observe(
            rows[episode.end].ts + timedelta(hours=1), None, episode.target_c, None, None
        )
    return None


@dataclass(frozen=True)
class RoomReport:
    """Per room figures over the clean, scored runs."""

    room: str
    episodes: int
    scored: int  # runs that reached the target
    clean: int
    mae: float | None
    bias: float | None
    cal_mae: float | None  # published (calibrated) figures, same runs
    cal_bias: float | None
    active_runs: int  # clean runs where the factor was already active
    active_mae: float | None  # raw figures on those runs
    active_cal_mae: float | None  # calibrated figures on those runs
    final_factor: float


def _mean(values: Iterable[float]) -> float | None:
    listed = list(values)
    return sum(listed) / len(listed) if listed else None


def summarise(
    room: str,
    episodes: int,
    results: Sequence[RunResult],
    factors: Sequence[float],
    final_factor: float,
) -> RoomReport:
    """Aggregate. ``factors[i]`` is the factor the scorer had when ``results[i]`` opened."""
    clean = [(r, f) for r, f in zip(results, factors, strict=True) if r.clean]
    raw = [r.error_minutes for r, _ in clean if r.error_minutes is not None]
    cal = [r.published_error_minutes for r, _ in clean if r.published_error_minutes is not None]
    active = [r for r, f in clean if f != 1.0]
    return RoomReport(
        room=room,
        episodes=episodes,
        scored=sum(1 for r in results if r.error_minutes is not None),
        clean=len(clean),
        mae=_mean(abs(e) for e in raw),
        bias=_mean(raw),
        cal_mae=_mean(abs(e) for e in cal),
        cal_bias=_mean(cal),
        active_runs=len(active),
        active_mae=_mean(abs(r.error_minutes) for r in active if r.error_minutes is not None),
        active_cal_mae=_mean(
            abs(r.published_error_minutes) for r in active if r.published_error_minutes is not None
        ),
        final_factor=final_factor,
    )


# ------------------------------------------------------------------ model plumbing


class RollingModel:
    """A model refitted at most every ``refit_days`` on data strictly before the run."""

    def __init__(self, rows: Sequence[Sample], refit_days: float, history_days: float) -> None:
        self._rows = rows
        self._times = [r.ts for r in rows]
        self._refit = timedelta(days=refit_days)
        self._history = timedelta(days=history_days)
        self._model: RCThermalModel | None = None
        self._fitted_at: datetime | None = None
        self.fits = 0

    def model_at(self, now: datetime) -> RCThermalModel | None:
        """Model fitted before ``now`` (None while there is too little data)."""
        if self._fitted_at is None or now - self._fitted_at >= self._refit:
            self._model = self._fit(now)
            self._fitted_at = now
        return self._model

    def _fit(self, now: datetime) -> RCThermalModel | None:
        lo = bisect.bisect_left(self._times, now - self._history)
        hi = bisect.bisect_left(self._times, now)  # strictly before
        window = self._rows[lo:hi]
        if not window:
            return None
        model = RCThermalModel(ModelConfig(history_days=self._history.days))
        model.load_history(window, replace=True)
        self.fits += 1
        model.fit(window[-1].ts)
        return model if model.parameters() is not None else None


def backtest_room(
    room: str,
    rows: Sequence[Sample],
    refit_days: float = REFIT_DAYS,
    history_days: float = HISTORY_DAYS,
    effort_high: float = EFFORT_HIGH,
    min_rise_k: float = MIN_RISE_K,
) -> RoomReport:
    """Run the whole back-test for one room."""
    episodes = find_episodes(rows, effort_high=effort_high, min_rise_k=min_rise_k)
    rolling = RollingModel(rows, refit_days, history_days)
    scorer = RunScorer()
    results: list[RunResult] = []
    factors: list[float] = []

    def predict(row: Sample) -> float | None:
        model = rolling.model_at(row.ts)
        if model is None or row.indoor_c is None:
            return None
        return model.preheat(row.ts, row.indoor_c, target[0], row.outdoor_c).minutes

    target = [0.0]
    for episode in episodes:
        target[0] = episode.target_c
        factor = scorer.calibration().factor
        result = score_episode(scorer, rows, episode, predict)
        if result is not None:
            results.append(result)
            factors.append(_factor_applied(result, factor))
    return summarise(room, len(episodes), results, factors, scorer.calibration().factor)


def _factor_applied(result: RunResult, fallback: float) -> float:
    """Factor used for a run, recovered from its published and raw figures."""
    if result.published_minutes is not None and result.predicted_minutes > 0:
        return result.published_minutes / result.predicted_minutes
    return fallback


# ------------------------------------------------------------------ output


def _fmt(value: float | None, spec: str = "+.1f") -> str:
    return "-" if value is None else format(value, spec)


def format_table(reports: Sequence[RoomReport]) -> str:
    """Plain-text table, errors are predicted minus actual (positive = too slow)."""
    head = (
        f"{'room':<16}{'episodes':>9}{'clean':>6}{'MAE':>7}{'bias':>7}"
        f"{'cal MAE':>9}{'cal bias':>9}{'active':>7}{'MAE*':>7}{'cal MAE*':>9}{'factor':>8}"
    )
    lines = [head, "-" * len(head)]
    lines.extend(
        (
            f"{r.room:<16}{r.episodes:>9}{r.clean:>6}{_fmt(r.mae, '.1f'):>7}{_fmt(r.bias):>7}"
            f"{_fmt(r.cal_mae, '.1f'):>9}{_fmt(r.cal_bias):>9}{r.active_runs:>7}"
            f"{_fmt(r.active_mae, '.1f'):>7}{_fmt(r.active_cal_mae, '.1f'):>9}"
            f"{r.final_factor:>8.2f}"
        )
        for r in reports
    )
    lines.append(
        f"* = only runs where the factor was active (>= {MIN_RUNS} earlier clean runs); "
        "errors in minutes, predicted minus actual"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", type=Path, help="JSON-lines history, one per room")
    parser.add_argument("--refit-days", type=float, default=REFIT_DAYS)
    parser.add_argument("--history-days", type=float, default=HISTORY_DAYS)
    parser.add_argument("--effort", type=float, default=EFFORT_HIGH)
    parser.add_argument("--min-rise", type=float, default=MIN_RISE_K)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    reports = [
        backtest_room(
            path.stem,
            load_rows(path),
            args.refit_days,
            args.history_days,
            args.effort,
            args.min_rise,
        )
        for path in args.files
    ]
    if args.json:
        print(json.dumps([r.__dict__ for r in reports], indent=2))
    else:
        print(format_table(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Live scoring of preheat predictions against what the room actually did.

The estimate is "minutes of full heating to reach the target from now". A run opens when
that estimate is first made while the zone is really heating and the room is below the
target; it closes when the room gets there (scored), the target changes, the data goes
missing or it takes far too long (none of those are scored, they say nothing about the
model). The score is the signed error ``predicted - actual`` in minutes: positive means
the model expected a longer warm-up than it took.

A shared boiler can stop heating a zone mid-run, which the model does not know about. The
fraction of the run the zone was heating is kept, and only runs that were heating for
most of it (``clean``) feed the mean errors.

Pure stdlib, no Home Assistant imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Final, cast

MIN_GAP_K: Final = 0.5  # the room must be this far below the target to open a run
MIN_PREDICTED_MINUTES: Final = 5.0  # shorter predictions are noise, not worth scoring
REACHED_MARGIN_K: Final = 0.2  # at the target within this many kelvin
TARGET_TOLERANCE_K: Final = 0.05  # a different target ends the run
TIMEOUT_FACTOR: Final = 3.0  # give up after this many times the prediction ...
TIMEOUT_MIN_MINUTES: Final = 180.0  # ... and never before this long
NO_DATA_MINUTES: Final = 30.0  # no observation for this long ends the run
MAX_ACCOUNTING_GAP_MINUTES: Final = 10.0  # longest interval credited to heating time
CLEAN_HEATING_FRACTION: Final = 0.8  # runs heating at least this much feed the means
MAX_RESULTS: Final = 20  # results kept

OUTCOME_REACHED: Final = "reached"
OUTCOME_TARGET_CHANGED: Final = "target_changed"
OUTCOME_NO_DATA: Final = "no_data"
OUTCOME_TIMEOUT: Final = "timeout"


@dataclass
class OpenRun:
    """A prediction waiting for its result."""

    started_at: datetime
    start_c: float
    target_c: float
    predicted_minutes: float
    peak_c: float
    heating_minutes: float = 0.0
    last_seen: datetime | None = None


@dataclass(frozen=True)
class RunResult:
    """A closed run. ``error_minutes`` is only set when the room reached the target."""

    started_at: datetime
    finished_at: datetime
    outcome: str
    start_c: float
    target_c: float
    peak_c: float
    predicted_minutes: float
    actual_minutes: float
    heating_fraction: float
    error_minutes: float | None

    @property
    def clean(self) -> bool:
        """Scored, and the zone was heating for most of it."""
        return self.error_minutes is not None and self.heating_fraction >= CLEAN_HEATING_FRACTION


@dataclass(frozen=True)
class ScoringSummary:
    """Aggregate over the kept results."""

    runs: int  # results kept, any outcome
    reached: int
    clean: int
    mae_minutes: float | None  # over clean runs
    bias_minutes: float | None  # mean signed error over clean runs
    last: RunResult | None  # newest closed run
    open_run: OpenRun | None


def _minutes(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60.0


class RunScorer:
    """Opens runs from live predictions and scores them when they finish."""

    def __init__(self) -> None:
        """Start with nothing open and no results."""
        self.open_run: OpenRun | None = None
        self.results: list[RunResult] = []
        self._dirty = False

    def reset(self) -> None:
        """Forget the open run and every result."""
        self.open_run = None
        self.results = []
        self._dirty = True

    def consume_dirty(self) -> bool:
        """True once after the persisted state changed (a run opened or closed)."""
        dirty, self._dirty = self._dirty, False
        return dirty

    def observe(
        self,
        now: datetime,
        indoor_c: float | None,
        target_c: float,
        predicted_minutes: float | None,
        heating: bool | None,
    ) -> RunResult | None:
        """Feed one update; returns the run that closed with it, if any.

        ``predicted_minutes`` is the current estimate (None when there is none),
        ``heating`` whether the zone is really heating now (None = unknown).
        """
        run = self.open_run
        if run is None:
            self._maybe_open(now, indoor_c, target_c, predicted_minutes, heating)
            return None
        if run.last_seen is not None and _minutes(run.last_seen, now) > NO_DATA_MINUTES:
            return self._close(now, OUTCOME_NO_DATA)
        if indoor_c is None:
            if run.last_seen is None or _minutes(run.last_seen, now) > NO_DATA_MINUTES:
                return self._close(now, OUTCOME_NO_DATA)
            return None
        if run.last_seen is not None and heating:
            run.heating_minutes += min(_minutes(run.last_seen, now), MAX_ACCOUNTING_GAP_MINUTES)
        run.last_seen = now
        run.peak_c = max(run.peak_c, indoor_c)
        if indoor_c >= run.target_c - REACHED_MARGIN_K:
            return self._close(now, OUTCOME_REACHED)
        if abs(target_c - run.target_c) > TARGET_TOLERANCE_K:
            return self._close(now, OUTCOME_TARGET_CHANGED)
        limit = max(TIMEOUT_MIN_MINUTES, TIMEOUT_FACTOR * run.predicted_minutes)
        if _minutes(run.started_at, now) > limit:
            return self._close(now, OUTCOME_TIMEOUT)
        return None

    def _maybe_open(
        self,
        now: datetime,
        indoor_c: float | None,
        target_c: float,
        predicted_minutes: float | None,
        heating: bool | None,
    ) -> None:
        if indoor_c is None or predicted_minutes is None or heating is not True:
            return
        if predicted_minutes < MIN_PREDICTED_MINUTES or target_c - indoor_c < MIN_GAP_K:
            return
        self.open_run = OpenRun(
            started_at=now,
            start_c=indoor_c,
            target_c=target_c,
            predicted_minutes=predicted_minutes,
            peak_c=indoor_c,
            last_seen=now,
        )
        self._dirty = True

    def _close(self, now: datetime, outcome: str) -> RunResult:
        run = self.open_run
        assert run is not None
        self.open_run = None
        actual = _minutes(run.started_at, now)
        fraction = min(1.0, run.heating_minutes / actual) if actual > 0 else 1.0
        result = RunResult(
            started_at=run.started_at,
            finished_at=now,
            outcome=outcome,
            start_c=run.start_c,
            target_c=run.target_c,
            peak_c=run.peak_c,
            predicted_minutes=run.predicted_minutes,
            actual_minutes=actual,
            heating_fraction=fraction,
            error_minutes=run.predicted_minutes - actual if outcome == OUTCOME_REACHED else None,
        )
        self.results.append(result)
        del self.results[:-MAX_RESULTS]
        self._dirty = True
        return result

    def summary(self) -> ScoringSummary:
        """Aggregate over the kept results."""
        clean = [r.error_minutes for r in self.results if r.clean and r.error_minutes is not None]
        return ScoringSummary(
            runs=len(self.results),
            reached=sum(1 for r in self.results if r.outcome == OUTCOME_REACHED),
            clean=len(clean),
            mae_minutes=sum(abs(e) for e in clean) / len(clean) if clean else None,
            bias_minutes=sum(clean) / len(clean) if clean else None,
            last=self.results[-1] if self.results else None,
            open_run=self.open_run,
        )

    # ------------------------------------------------------------------ persistence

    def to_dict(self) -> dict[str, Any]:
        """JSON-able state (datetimes as ISO strings)."""

        def encode(item: Any) -> dict[str, Any]:
            return {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in asdict(item).items()
            }

        return {
            "open": encode(self.open_run) if self.open_run else None,
            "results": [encode(r) for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: Any) -> RunScorer:
        """Restore, dropping anything unreadable instead of failing."""
        scorer = cls()
        if not isinstance(data, dict):
            return scorer
        stored = cast("dict[str, Any]", data)
        raw_open: Any = stored.get("open")
        if isinstance(raw_open, dict):
            try:
                scorer.open_run = OpenRun(**_decode(cast("dict[str, Any]", raw_open)))
            except (TypeError, ValueError):
                scorer.open_run = None
        raw_results: Any = stored.get("results")
        listed: list[Any] = cast("list[Any]", raw_results) if isinstance(raw_results, list) else []
        for raw in listed:
            if not isinstance(raw, dict):
                continue
            try:
                scorer.results.append(RunResult(**_decode(cast("dict[str, Any]", raw))))
            except (TypeError, ValueError):
                continue
        del scorer.results[:-MAX_RESULTS]
        return scorer


def _decode(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn the ISO strings of the datetime fields back into datetimes."""
    out = dict(raw)
    for key in ("started_at", "finished_at", "last_seen"):
        if isinstance(out.get(key), str):
            out[key] = datetime.fromisoformat(out[key])
    return out

"""Self-calibration: a multiplicative correction of the predicted preheat minutes.

Every clean scored run says how long the room really took (``actual``) against what the
model predicted (``predicted``, always the UNCALIBRATED figure, otherwise the factor would
feed back into its own evidence). The ratio ``actual / predicted`` is 1.0 for a perfect
model and above 1.0 when the model is too optimistic. The factor is the median of those
ratios (robust to the odd shared-boiler or open-window run), shrunk towards 1.0 as if
``PRIOR_RUNS`` extra runs had a ratio of exactly 1.0, clamped to a sane range, and only
active once ``MIN_RUNS`` clean runs exist. With weak evidence it stays at 1.0.

Pure stdlib, no Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median
from typing import Final

MIN_RUNS: Final = 5  # clean runs needed before the factor leaves 1.0
PRIOR_RUNS: Final = 3.0  # weight of the prior "the model is right", in runs
FACTOR_MIN: Final = 0.5
FACTOR_MAX: Final = 2.0
RATIO_MIN: Final = 0.2  # a single run never counts for less than this ...
RATIO_MAX: Final = 5.0  # ... or more than this (guards the median against absurd inputs)


@dataclass(frozen=True)
class Calibration:
    """The factor and how many clean runs it rests on."""

    factor: float = 1.0
    runs: int = 0

    @property
    def active(self) -> bool:
        """True once enough evidence exists for the factor to differ from 1.0."""
        return self.runs >= MIN_RUNS


def calibration_factor(pairs: Iterable[tuple[float, float]]) -> Calibration:
    """Factor from ``(raw predicted minutes, actual minutes)`` of clean, scored runs.

    Pairs with a non-positive figure are ignored. Returns factor 1.0 below ``MIN_RUNS``.
    """
    ratios = [
        min(RATIO_MAX, max(RATIO_MIN, actual / predicted))
        for predicted, actual in pairs
        if predicted > 0 and actual > 0
    ]
    n = len(ratios)
    if n < MIN_RUNS:
        return Calibration(1.0, n)
    shrunk = (n * median(ratios) + PRIOR_RUNS) / (n + PRIOR_RUNS)
    return Calibration(min(FACTOR_MAX, max(FACTOR_MIN, shrunk)), n)


def apply_factor(minutes: float | None, factor: float) -> float | None:
    """Scale a predicted duration (the whole figure, dead time included)."""
    return None if minutes is None else minutes * factor

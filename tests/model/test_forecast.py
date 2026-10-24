"""Forecast averaging and the duration solve."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rtm_core import ForecastPoint
from rtm_core.forecast import mean_outdoor, solve_duration

NOW = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)


def h(x: float) -> datetime:
    return NOW + timedelta(hours=x)


def test_constant_and_empty():
    assert mean_outdoor([], NOW, h(2)) is None
    assert mean_outdoor([], NOW, h(2), current=4.0) == 4.0
    assert mean_outdoor([ForecastPoint(h(1), 3.0)], NOW, h(5)) == 3.0  # clamped both sides


def test_linear_ramp_trapezoid():
    fc = [ForecastPoint(h(0), 0.0), ForecastPoint(h(4), 8.0)]
    assert mean_outdoor(fc, NOW, h(4)) == pytest.approx(4.0)
    assert mean_outdoor(fc, NOW, h(2)) == pytest.approx(2.0)
    # edge interpolation inside a segment: mean over [1 h, 3 h] = value at 2 h
    assert mean_outdoor(fc, h(1), h(3)) == pytest.approx(4.0)


def test_clamping_after_last_point():
    fc = [ForecastPoint(h(0), 0.0), ForecastPoint(h(2), 4.0)]
    # ramp mean 2 over the first 2 h, then 4 for 2 h → 3
    assert mean_outdoor(fc, NOW, h(4)) == pytest.approx(3.0)


def test_current_reading_anchors_start():
    fc = [ForecastPoint(h(-1), 100.0), ForecastPoint(h(2), 6.0)]
    # current 0 at now, ramp to 6 at 2 h → mean 3; the stale point is ignored
    assert mean_outdoor(fc, NOW, h(2), current=0.0) == pytest.approx(3.0)
    assert mean_outdoor(fc, NOW, NOW, current=0.0) == 0.0


def test_solve_duration_fixed_point_matches_brute_force():
    # duration (min) shrinks with warmer outdoor; outdoor warms 2 K/h
    def minutes_for(tout: float) -> float:
        return 120.0 - 5.0 * tout

    def outdoor_for(m: float) -> float:
        return 0.0 + 2.0 * (m / 60.0) / 2.0  # mean of a ramp over [0, m]

    d, tout, _info = solve_duration(minutes_for, outdoor_for)
    assert abs(d - minutes_for(outdoor_for(d))) < 0.6
    # exact: d = 120 − 5·d/60 → d = 120 / (1 + 5/60)
    assert d == pytest.approx(120 / (1 + 5 / 60), abs=0.6)
    assert tout == pytest.approx(outdoor_for(d), abs=0.05)


def test_solve_duration_bisection_when_fixed_point_oscillates():
    # strongly coupled: plain iteration diverges/oscillates, bisection must converge
    def minutes_for(tout: float) -> float:
        return max(0.0, 300.0 - 40.0 * tout)

    def outdoor_for(m: float) -> float:
        return m / 60.0  # mean outdoor grows 1 K per hour of horizon

    d, _, info = solve_duration(minutes_for, outdoor_for)
    assert info["method"] == "bisection"
    assert abs(d - minutes_for(outdoor_for(d))) < 0.5


def test_solve_duration_unreachable():
    d, tout, _info = solve_duration(lambda _t: None, lambda _m: -20.0)
    assert d is None
    assert tout == -20.0

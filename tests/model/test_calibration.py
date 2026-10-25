"""Self-calibration factor: robustness, shrinkage, clamping, minimum evidence."""

from __future__ import annotations

import pytest

from rtm_core.calibration import (
    FACTOR_MAX,
    FACTOR_MIN,
    MIN_RUNS,
    PRIOR_RUNS,
    Calibration,
    apply_factor,
    calibration_factor,
)


def pairs(ratio: float, n: int, predicted: float = 40.0) -> list[tuple[float, float]]:
    return [(predicted, predicted * ratio)] * n


def test_no_runs_is_neutral() -> None:
    cal = calibration_factor([])
    assert cal == Calibration(1.0, 0)
    assert cal.active is False


def test_below_minimum_runs_stays_at_one() -> None:
    cal = calibration_factor(pairs(1.5, MIN_RUNS - 1))
    assert cal.factor == 1.0
    assert cal.runs == MIN_RUNS - 1
    assert cal.active is False


def test_at_minimum_runs_is_shrunk_towards_one() -> None:
    cal = calibration_factor(pairs(1.5, MIN_RUNS))
    expected = (MIN_RUNS * 1.5 + PRIOR_RUNS) / (MIN_RUNS + PRIOR_RUNS)
    assert cal.active is True
    assert cal.factor == pytest.approx(expected)
    assert 1.0 < cal.factor < 1.5


def test_more_evidence_moves_closer_to_the_median() -> None:
    few = calibration_factor(pairs(1.4, MIN_RUNS)).factor
    many = calibration_factor(pairs(1.4, 40)).factor
    assert 1.0 < few < many < 1.4


def test_unbiased_model_stays_near_one() -> None:
    cal = calibration_factor([(40.0, 38.0), (40.0, 42.0)] * 5)
    assert cal.factor == pytest.approx(1.0, abs=0.02)


def test_outliers_do_not_move_the_median_much() -> None:
    clean = calibration_factor(pairs(1.2, 9)).factor
    spoiled = calibration_factor([*pairs(1.2, 9), (40.0, 4000.0)]).factor
    assert abs(spoiled - clean) < 0.05


def test_extreme_ratios_are_clamped_per_run() -> None:
    cal = calibration_factor([(10.0, 10_000.0)] * 8)
    assert cal.factor <= FACTOR_MAX


def test_factor_is_clamped() -> None:
    assert calibration_factor(pairs(5.0, 500)).factor == FACTOR_MAX
    assert calibration_factor(pairs(0.2, 500)).factor == FACTOR_MIN


def test_nonpositive_figures_are_ignored() -> None:
    cal = calibration_factor([(0.0, 10.0), (10.0, 0.0), (-5.0, 5.0), *pairs(1.0, 3)])
    assert cal.runs == 3
    assert cal.factor == 1.0


def test_apply_factor_scales_the_whole_figure() -> None:
    assert apply_factor(40.0, 1.25) == 50.0
    assert apply_factor(None, 1.25) is None

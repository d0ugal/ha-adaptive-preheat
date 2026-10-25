"""Run scoring: when a run opens, how it closes and how it is scored."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rtm_core.scoring import (
    MAX_RESULTS,
    OUTCOME_NO_DATA,
    OUTCOME_REACHED,
    OUTCOME_TARGET_CHANGED,
    OUTCOME_TIMEOUT,
    RunScorer,
)

T0 = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def opened(predicted: float = 40.0, target: float = 20.0, start: float = 18.0) -> RunScorer:
    scorer = RunScorer()
    scorer.observe(at(0), start, target, predicted, True)
    assert scorer.open_run is not None
    return scorer


def run_to_target(scorer: RunScorer, minutes: int, *, heating: bool = True):
    """Observe every 5 minutes up to ``minutes``, then reach the target."""
    for minute in range(5, minutes, 5):
        assert scorer.observe(at(minute), 19.0, 20.0, None, heating) is None
    return scorer.observe(at(minutes), 20.0, 20.0, None, heating)


@pytest.mark.parametrize(
    ("indoor", "target", "predicted", "heating"),
    [
        (None, 20.0, 40.0, True),  # no room temperature
        (18.0, 20.0, None, True),  # no estimate (learning, unreachable, at target)
        (18.0, 20.0, 40.0, False),  # zone not heating: the estimate assumes full effort
        (18.0, 20.0, 40.0, None),  # heating unknown
        (19.8, 20.0, 40.0, True),  # barely below the target
        (18.0, 20.0, 3.0, True),  # trivial prediction
    ],
)
def test_run_does_not_open(indoor, target, predicted, heating) -> None:
    scorer = RunScorer()
    assert scorer.observe(at(0), indoor, target, predicted, heating) is None
    assert scorer.open_run is None
    assert scorer.consume_dirty() is False


def test_run_opens_once_and_keeps_its_first_prediction() -> None:
    scorer = opened(predicted=40.0)
    assert scorer.consume_dirty() is True
    assert scorer.consume_dirty() is False
    scorer.observe(at(5), 18.4, 20.0, 99.0, True)
    assert scorer.open_run.predicted_minutes == 40.0
    assert scorer.open_run.start_c == 18.0
    assert scorer.open_run.peak_c == 18.4


def test_reached_run_is_scored_with_a_signed_error() -> None:
    scorer = opened(predicted=40.0)
    result = run_to_target(scorer, 30)
    assert result is not None
    assert result.outcome == OUTCOME_REACHED
    assert result.actual_minutes == 30
    assert result.error_minutes == 10  # predicted 40, took 30: model too slow
    assert result.heating_fraction == 1.0
    assert result.clean is True
    assert scorer.open_run is None


def test_reached_within_margin_counts() -> None:
    scorer = opened(target=20.0)
    result = scorer.observe(at(25), 19.85, 20.0, None, True)
    assert result is not None
    assert result.outcome == OUTCOME_REACHED


def test_a_run_that_was_mostly_not_heating_is_not_clean() -> None:
    scorer = opened(predicted=40.0)
    result = run_to_target(scorer, 30, heating=False)
    assert result is not None
    assert result.error_minutes == 10
    assert result.heating_fraction == 0.0
    assert result.clean is False
    assert scorer.summary().clean == 0
    assert scorer.summary().mae_minutes is None


def test_slow_runs_give_negative_errors() -> None:
    scorer = opened(predicted=20.0)
    result = run_to_target(scorer, 35)
    assert result is not None
    assert result.error_minutes == -15


def test_target_change_aborts_without_a_score() -> None:
    scorer = opened(target=20.0)
    scorer.observe(at(10), 18.5, 20.0, None, True)
    result = scorer.observe(at(15), 18.7, 14.0, None, True)
    assert result is not None
    assert result.outcome == OUTCOME_TARGET_CHANGED
    assert result.error_minutes is None
    assert result.peak_c == 18.7
    assert result.clean is False


def test_reaching_the_old_target_wins_over_a_changed_target() -> None:
    scorer = opened(target=20.0)
    result = scorer.observe(at(30), 20.0, 21.0, None, True)
    assert result is not None
    assert result.outcome == OUTCOME_REACHED


def test_timeout_after_three_times_the_prediction() -> None:
    scorer = opened(predicted=100.0)
    for minute in range(5, 300, 5):
        assert scorer.observe(at(minute), 18.5, 20.0, None, True) is None
    result = scorer.observe(at(305), 18.5, 20.0, None, True)
    assert result is not None
    assert result.outcome == OUTCOME_TIMEOUT
    assert result.error_minutes is None


def test_timeout_never_shorter_than_three_hours() -> None:
    scorer = opened(predicted=10.0)
    for minute in range(5, 180, 5):
        assert scorer.observe(at(minute), 18.5, 20.0, None, True) is None
    assert scorer.observe(at(185), 18.5, 20.0, None, True).outcome == OUTCOME_TIMEOUT


def test_missing_temperature_for_too_long_ends_the_run() -> None:
    scorer = opened()
    assert scorer.observe(at(10), None, 20.0, None, True) is None
    result = scorer.observe(at(45), None, 20.0, None, True)
    assert result is not None
    assert result.outcome == OUTCOME_NO_DATA


def test_long_silence_ends_the_run_even_when_data_returns() -> None:
    scorer = opened()
    result = scorer.observe(at(120), 20.0, 20.0, None, True)
    assert result is not None
    assert result.outcome == OUTCOME_NO_DATA  # not scored as a 120 minute warm-up


def test_summary_means_cover_clean_runs_only() -> None:
    scorer = RunScorer()
    for predicted, took, heating in ((40.0, 30, True), (20.0, 35, True), (50.0, 10, False)):
        base = 1000 * (len(scorer.results) + 1)
        scorer.observe(at(base), 18.0, 20.0, predicted, True)
        for minute in range(5, took, 5):
            scorer.observe(at(base + minute), 19.0, 20.0, None, heating)
        scorer.observe(at(base + took), 20.0, 20.0, None, heating)
    summary = scorer.summary()
    assert (summary.runs, summary.reached, summary.clean) == (3, 3, 2)
    assert summary.mae_minutes == pytest.approx((10 + 15) / 2)
    assert summary.bias_minutes == pytest.approx((10 - 15) / 2)
    assert summary.last is scorer.results[-1]
    assert summary.open_run is None


def test_empty_summary() -> None:
    summary = RunScorer().summary()
    assert (summary.runs, summary.clean, summary.last) == (0, 0, None)
    assert summary.mae_minutes is None
    assert summary.bias_minutes is None


def test_results_are_capped() -> None:
    scorer = RunScorer()
    for index in range(MAX_RESULTS + 5):
        base = 1000 * index
        scorer.observe(at(base), 18.0, 20.0, 40.0, True)
        scorer.observe(at(base + 10), 20.0, 20.0, None, True)
    assert len(scorer.results) == MAX_RESULTS
    assert scorer.results[-1].started_at == at(1000 * (MAX_RESULTS + 4))


def test_round_trip_keeps_results_and_the_open_run() -> None:
    scorer = opened(predicted=40.0)
    run_to_target(scorer, 30)
    scorer.observe(at(2000), 18.0, 20.0, 25.0, True)
    restored = RunScorer.from_dict(scorer.to_dict())
    assert restored.results == scorer.results
    assert restored.open_run == scorer.open_run
    assert restored.summary() == scorer.summary()


@pytest.mark.parametrize(
    "data",
    [
        None,
        "nonsense",
        {},
        {"open": "x", "results": "y"},
        {"open": {"started_at": "not a date"}, "results": [{"bad": 1}, 3]},
    ],
)
def test_unreadable_state_is_dropped(data) -> None:
    scorer = RunScorer.from_dict(data)
    assert scorer.open_run is None
    assert scorer.results == []


def test_reset_forgets_everything() -> None:
    scorer = opened()
    run_to_target(scorer, 30)
    scorer.observe(at(2000), 18.0, 20.0, 25.0, True)
    scorer.consume_dirty()
    scorer.reset()
    assert scorer.open_run is None
    assert scorer.results == []
    assert scorer.consume_dirty() is True

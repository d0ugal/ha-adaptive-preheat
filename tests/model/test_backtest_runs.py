"""Pure parts of scripts/backtest_runs.py: parsing, regridding, episodes, aggregation."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rtm_core import RunScorer, Sample
from rtm_core import synthetic as syn

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backtest_runs.py"
_spec = importlib.util.spec_from_file_location("backtest_runs", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
bt = importlib.util.module_from_spec(_spec)
sys.modules["backtest_runs"] = bt
_spec.loader.exec_module(bt)

T0 = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)


def row(minute: float, indoor: float | None, effort: float | None, outdoor: float | None = 5.0):
    return Sample(T0 + timedelta(minutes=minute), indoor, outdoor, effort)


def ramp(start_c: float = 17.0, minutes: int = 60, rate: float = 0.05) -> list[Sample]:
    """Heating at full effort, then idle."""
    rows = [row(m, start_c + rate * m, 1.0) for m in range(0, minutes + 1, 5)]
    peak = rows[-1].indoor_c
    rows += [row(minutes + m, peak, 0.0) for m in range(5, 40, 5)]
    return rows


def test_parse_row_handles_nulls(tmp_path: Path) -> None:
    line = json.dumps({"ts": "2026-01-05T06:00:00+00:00", "indoor_c": 18, "outdoor_c": None})
    parsed = bt.parse_row(line)
    assert parsed.indoor_c == 18.0
    assert parsed.outdoor_c is None
    assert parsed.effort is None
    path = tmp_path / "room.jsonl"
    path.write_text(line + "\n\n")
    assert len(bt.load_rows(path)) == 1


def test_regrid_merges_unaligned_fields_and_expires_old_values() -> None:
    rows = [
        Sample(T0 + timedelta(minutes=1), 18.0, None, None),
        Sample(T0 + timedelta(minutes=2), None, 4.0, 0.7),
        Sample(T0 + timedelta(minutes=40), None, None, 0.2),
    ]
    grid = bt.regrid(rows)
    first = grid[0]
    assert (first.indoor_c, first.outdoor_c, first.effort) == (18.0, 4.0, 0.7)
    late = grid[-1]
    assert late.indoor_c is None  # 38 minutes old
    assert late.outdoor_c == 4.0  # outdoor is carried longer
    assert late.effort == 0.2
    assert bt.regrid([]) == []


def test_regrid_drops_empty_slots() -> None:
    rows = [Sample(T0, None, None, None), Sample(T0 + timedelta(minutes=30), 18.0, None, None)]
    assert all(
        s.indoor_c is not None or s.effort is not None or s.outdoor_c is not None
        for s in bt.regrid(rows)
    )


def test_find_episodes_uses_peak_as_target_and_needs_a_rise() -> None:
    episodes = bt.find_episodes(ramp(rate=0.05))
    assert len(episodes) == 1
    assert episodes[0].target_c == pytest.approx(20.0)
    assert bt.find_episodes(ramp(rate=0.01)) == []  # only 0.6 K


def test_find_episodes_bridges_short_pauses_and_splits_long_ones() -> None:
    rows = [row(m, 17.0 + 0.05 * m, 1.0) for m in range(0, 40, 5)]
    rows += [row(40, 19.0, 0.0), row(45, 19.0, 1.0)]
    rows += [row(m, 19.0 + 0.05 * (m - 45), 1.0) for m in range(50, 90, 5)]
    assert len(bt.find_episodes(rows)) == 1
    gap = [row(m, 17.0 + 0.05 * m, 1.0) for m in range(0, 40, 5)]
    gap += [row(m, 19.0, 0.0) for m in range(40, 100, 5)]
    gap += [row(m, 19.0 + 0.05 * (m - 100), 1.0) for m in range(100, 140, 5)]
    assert len(bt.find_episodes(gap)) == 2


def test_find_episodes_ignores_rows_without_indoor() -> None:
    rows = [row(m, None, 1.0) for m in range(0, 60, 5)]
    assert bt.find_episodes(rows) == []


def test_score_episode_runs_the_real_scorer() -> None:
    rows = ramp()
    episode = bt.find_episodes(rows)[0]
    scorer = RunScorer()
    result = bt.score_episode(scorer, rows, episode, lambda _r: 80.0)
    assert result is not None
    assert result.error_minutes is not None
    assert result.predicted_minutes == 80.0
    assert result.clean


def test_score_episode_skips_runs_without_a_prediction() -> None:
    rows = ramp()
    episode = bt.find_episodes(rows)[0]
    assert bt.score_episode(RunScorer(), rows, episode, lambda _r: None) is None


def test_score_episode_publishes_the_calibrated_figure() -> None:
    scorer = RunScorer()
    rows = ramp()
    episode = bt.find_episodes(rows)[0]
    for k in range(6):  # earlier runs: the model was 2x too slow
        shifted = [
            Sample(r.ts + timedelta(days=k), r.indoor_c, r.outdoor_c, r.effort) for r in rows
        ]
        bt.score_episode(scorer, shifted, episode, lambda _r: 120.0)
    assert scorer.calibration().active
    late = [Sample(r.ts + timedelta(days=9), r.indoor_c, r.outdoor_c, r.effort) for r in rows]
    result = bt.score_episode(scorer, late, episode, lambda _r: 120.0)
    assert result is not None
    assert result.published_minutes < result.predicted_minutes
    assert result.error_minutes == result.predicted_minutes - result.actual_minutes


def test_summarise_separates_raw_and_calibrated_errors() -> None:
    scorer = RunScorer()
    rows = ramp()
    episode = bt.find_episodes(rows)[0]
    results, factors = [], []
    for k in range(8):
        shifted = [
            Sample(r.ts + timedelta(days=k), r.indoor_c, r.outdoor_c, r.effort) for r in rows
        ]
        factor = scorer.calibration().factor
        results.append(bt.score_episode(scorer, shifted, episode, lambda _r: 120.0))
        factors.append(factor)
    report = bt.summarise("room_a", 8, results, factors, scorer.calibration().factor)
    assert report.clean == 8
    assert report.active_runs == 3
    assert report.mae > report.cal_mae
    assert report.active_mae > report.active_cal_mae
    assert report.bias > 0
    text = bt.format_table([report])
    assert "room_a" in text


def test_summarise_without_runs() -> None:
    report = bt.summarise("empty", 0, [], [], 1.0)
    assert report.mae is None
    assert "-" in bt.format_table([report])


def test_factor_applied_falls_back_for_old_results() -> None:
    scorer = RunScorer()
    rows = ramp()
    result = bt.score_episode(scorer, rows, bt.find_episodes(rows)[0], lambda _r: 80.0)
    assert bt._factor_applied(result, 9.0) == pytest.approx(1.0)
    old = type(result)(**{**result.__dict__, "published_minutes": None})
    assert bt._factor_applied(old, 9.0) == 9.0


def test_end_to_end_on_a_synthetic_room(tmp_path: Path) -> None:
    run = syn.generate(syn.radiator_room(), 25)
    path = tmp_path / "bedroom_1.jsonl"
    with path.open("w") as handle:
        for s in run.samples[::5]:
            handle.write(
                json.dumps(
                    {
                        "ts": s.ts.isoformat(),
                        "indoor_c": s.indoor_c,
                        "outdoor_c": s.outdoor_c,
                        "effort": s.effort,
                    }
                )
                + "\n"
            )
    assert bt.main([str(path), "--refit-days", "10"]) == 0
    assert bt.main([str(path), "--json"]) == 0
    report = bt.backtest_room("bedroom_1", bt.load_rows(path), refit_days=10)
    assert report.episodes > 0
    assert report.clean > 0
    assert report.mae is not None
    assert report.mae < 60


def test_shape_effort_raises_the_effort_to_a_power() -> None:
    assert bt.shape_effort(row(0, 18.0, 0.25), 1.0).effort == 0.25
    assert bt.shape_effort(row(0, 18.0, 0.25), 0.5).effort == pytest.approx(0.5)
    assert bt.shape_effort(row(0, 18.0, None), 0.5).effort is None
    assert bt.shape_effort(row(0, 18.0, -0.1), 0.5).effort == 0.0  # never a complex number


def test_step_and_effort_power_reach_the_model(tmp_path: Path) -> None:
    """Both options change the model's input, not the episodes it is scored on."""
    run = syn.generate(syn.radiator_room(), 25)
    path = tmp_path / "bedroom_1.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "ts": s.ts.isoformat(),
                    "indoor_c": s.indoor_c,
                    "outdoor_c": s.outdoor_c,
                    "effort": s.effort,
                }
            )
            + "\n"
            for s in run.samples[::5]
        )
    )
    rows = bt.load_rows(path)
    base = bt.backtest_room("bedroom_1", rows, refit_days=10, step_minutes=5.0)
    other = bt.backtest_room("bedroom_1", rows, refit_days=10, step_minutes=15.0, effort_power=0.5)
    assert other.episodes == base.episodes  # found on the raw effort either way
    assert other.mae is not None
    assert other.mae != base.mae  # the model really saw different input
    assert bt.main([str(path), "--step-minutes", "15", "--effort-power", "0.5"]) == 0
    assert bt.RollingModel(rows, 10, 30)._step == bt.RECOMMENDED_STEP_MINUTES

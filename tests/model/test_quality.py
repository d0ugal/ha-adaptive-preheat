"""Quality report, maturity and timing."""

from __future__ import annotations

import time

import pytest

from rtm_core import ModelConfig, RCThermalModel
from rtm_core.quality import bad_rows, data_factor, heat_up_episodes, maturity, skill_factor


@pytest.mark.parametrize("name", ["radiator", "fast_radiator", "underfloor", "no_outdoor"])
def test_rmse_beats_persistence_after_training(trained, name):
    q = trained(name, 14, True).quality()
    assert q.rmse_c is not None and q.baseline_rmse_c is not None
    assert q.rmse_c < q.baseline_rmse_c
    assert q.heating_hours > 20 and q.cooling_hours > 40
    assert 0.0 <= q.maturity <= 1.0


def test_duration_mae_on_heat_up_episodes(trained):
    q = trained("radiator", 14).quality()
    assert q.episodes >= 3
    assert q.duration_mae_minutes < 10.0


def test_maturity_rises_with_days(trained):
    mats = [trained("radiator", d, True).quality().maturity for d in (1, 3, 14)]
    assert mats[0] < mats[1] <= mats[2]
    assert mats[0] < 0.5 and mats[2] > 0.8


def test_maturity_formula():
    assert data_factor(10, 20) == pytest.approx(0.25)
    assert data_factor(40, 80) == 1.0
    assert skill_factor(0.5, 1.0) == 1.0
    assert skill_factor(0.8, 1.0) == pytest.approx(0.4)
    assert skill_factor(1.2, 1.0) == 0.0
    assert skill_factor(None, 1.0) == 0.0
    assert maturity(10, 20, 0.8, 1.0) == pytest.approx(0.1)


def test_episode_detection(trained):
    g = trained("radiator", 14)._grid
    eps = heat_up_episodes(g, bad_rows(g, 1.5), 0, 5)
    assert len(eps) >= 10
    for k0, k1, kr in eps:
        assert k0 <= kr <= k1
        assert g.indoor[k1] - g.indoor[k0] >= 0.5


def test_quality_before_fit():
    q = RCThermalModel(ModelConfig()).quality()
    assert q.maturity == 0.0 and q.samples == 0 and q.rmse_c is None


def test_fit_time_30_days(run_of):
    """30 days of 5-min data (8640 steps × 19 dead-time candidates × IRLS) < 2 s."""
    run = run_of("radiator", 30, True)
    m = RCThermalModel(ModelConfig())
    m.load_history(run.samples, replace=True)
    assert len(m._grid) >= 8600
    t0 = time.perf_counter()
    s = m.fit(run.samples[-1].ts)
    elapsed = time.perf_counter() - t0
    print(f"\n30-day fit: {elapsed:.3f} s ({s['rows']} rows)")
    assert s["status"] == "ok"
    assert elapsed < 2.0

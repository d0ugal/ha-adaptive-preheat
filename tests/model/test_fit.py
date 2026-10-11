"""Parameter recovery, robustness and fitting internals."""

from __future__ import annotations

import pytest

from rtm_core import ModelConfig, RCThermalModel
from rtm_core import synthetic as syn
from rtm_core.cooling import cooling_tau
from rtm_core.deadtime import tangent_dead_time
from rtm_core.fit import A_MIN, B_MIN, solve_linear, solve_projected


def _rel(est: float, true: float) -> float:
    return abs(est - true) / true


@pytest.mark.parametrize("name", ["radiator", "fast_radiator", "underfloor"])
def test_parameter_recovery_clean(trained, name):
    spec = syn.ROOMS[name]()
    p = trained(name, 14).parameters()
    assert p is not None
    assert _rel(p.heating_rate_k_per_h, spec.a) < 0.15
    assert _rel(p.loss_rate_per_h, spec.b) < 0.15
    assert abs(p.dead_time_minutes - spec.dead_time_minutes) <= 10


@pytest.mark.parametrize("name", ["radiator", "fast_radiator", "underfloor"])
def test_parameter_recovery_with_unflagged_windows(trained, run_of, name):
    spec = syn.ROOMS[name]()
    assert run_of(name, 14, True).windows, "scenario must contain window openings"
    p = trained(name, 14, True).parameters()
    assert _rel(p.heating_rate_k_per_h, spec.a) < 0.25
    assert _rel(p.loss_rate_per_h, spec.b) < 0.25
    assert abs(p.dead_time_minutes - spec.dead_time_minutes) <= 15


def test_no_outdoor_room_learns_equilibrium(trained):
    m = trained("no_outdoor", 14)
    s = m.last_fit_summary()
    assert s["mode"] == "no_outdoor"
    spec = syn.no_outdoor_room()
    p = m.parameters()
    assert _rel(p.heating_rate_k_per_h, spec.a) < 0.25
    assert abs(p.dead_time_minutes - spec.dead_time_minutes) <= 10


def test_fit_summary_is_reported(trained):
    s = trained("radiator", 14).last_fit_summary()
    assert s["status"] == "ok" and s["mode"] == "outdoor"
    assert set(s["criterion"]) == {str(m) for m in range(0, 91, 5)}
    assert "cooling_tau_h" in s["cross_check"] and "tangent_dead_time_min" in s["cross_check"]


def test_insufficient_data(run_of):
    run = run_of("radiator", 2)
    m = RCThermalModel(ModelConfig())
    m.load_history(run.samples[:60], replace=True)  # one hour
    assert not m.fit_due(run.samples[59].ts)
    s = m.fit(run.samples[59].ts)
    assert s["status"] == "insufficient_data"
    assert m.parameters() is None


def test_fit_due_cadence(run_of):
    run = run_of("radiator", 2)
    m = RCThermalModel(ModelConfig())
    m.load_history(run.samples[: 6 * 60], replace=True)
    now = run.samples[6 * 60 - 1].ts
    assert m.fit_due(now)
    m.fit(now)
    assert not m.fit_due(now)
    later = run.samples[7 * 60].ts
    assert not m.fit_due(later)  # no new data yet
    for s in run.samples[6 * 60 : 7 * 60 + 1]:
        m.add_sample(s)
    assert m.fit_due(later)


def test_weak_excitation_uses_cross_check_fallbacks():
    # 18 h, mostly free cooling: < 3 h of heating → b taken from cooling segments
    spec = syn.radiator_room(eco_c=15.0, comfort_c=15.0, front_amp=0.5)
    run = syn.generate(spec, 0.75, initial_temp=20.0, windows=False, disturbances=False)
    m = RCThermalModel(ModelConfig())
    m.load_history(run.samples, replace=True)
    s = m.fit(run.samples[-1].ts)
    assert s["status"] == "ok"
    assert "loss_rate_from_cooling" in s["fallbacks"]
    assert m.parameters().loss_rate_per_h == pytest.approx(
        1 / s["cross_check"]["cooling_tau_h"], rel=1e-3
    )


def test_cross_checks_on_synthetic_room(trained):
    g = trained("radiator", 14)._grid
    cool = cooling_tau(g)
    dead = tangent_dead_time(g)
    assert cool is not None and 5 < cool.tau_hours < 25
    assert dead is not None and 5 < dead.minutes < 60


def test_solve_linear_and_projection():
    m = [[4.0, 1.0, 0.0], [1.0, 3.0, 1.0], [0.0, 1.0, 2.0]]
    x = solve_linear(m, [1.0, 2.0, 3.0])
    for i in range(3):
        assert sum(m[i][j] * x[j] for j in range(3)) == pytest.approx([1.0, 2.0, 3.0][i])
    assert solve_linear([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]) is None
    # unconstrained optimum has a < 0 and b < 0 → projected and re-solved
    eye = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    theta, proj = solve_projected(eye, [-1.0, -2.0, 0.5])
    assert theta == [A_MIN, B_MIN, 0.5] and set(proj) == {"a", "b"}

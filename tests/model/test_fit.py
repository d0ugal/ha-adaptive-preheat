"""Parameter recovery, robustness and fitting internals."""

from __future__ import annotations

from dataclasses import replace

import pytest

from rtm_core import ModelConfig, RCThermalModel
from rtm_core import fit as fit_module
from rtm_core import synthetic as syn
from rtm_core.cooling import cooling_tau
from rtm_core.deadtime import tangent_dead_time
from rtm_core.fit import (
    A_MIN,
    B_MIN,
    Dataset,
    build_dataset,
    fit_rc,
    solve_linear,
    solve_projected,
)


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
def test_gains_c_recovered(trained, name):
    spec = syn.ROOMS[name]()
    p = trained(name, 14).parameters()
    # c is an additive term (0.1-0.5 K/h here) that the fit trades off against b·(T - Tout),
    # so its relative error reaches ~30 % (the absolute error stays ~0.15 K/h); assert an
    # absolute tolerance, which is still small against the heating gain a (>= 1.5 K/h)
    assert abs(p.gains_k_per_h - spec.c) < 0.2


@pytest.mark.parametrize("name", ["radiator", "fast_radiator", "underfloor"])
def test_parameter_recovery_with_unflagged_windows(trained, run_of, name):
    spec = syn.ROOMS[name]()
    assert run_of(name, 14, True).windows, "scenario must contain window openings"
    p = trained(name, 14, True).parameters()
    assert _rel(p.heating_rate_k_per_h, spec.a) < 0.15
    assert _rel(p.loss_rate_per_h, spec.b) < 0.15
    assert abs(p.dead_time_minutes - spec.dead_time_minutes) <= 15


@pytest.mark.parametrize("name", ["radiator", "fast_radiator"])
def test_glitch_guard_drops_single_step_spikes(name):
    spec = syn.ROOMS[name]()
    run = syn.generate(spec, 14, windows=False, disturbances=False)

    def fitted(samples):
        m = RCThermalModel(ModelConfig())
        m.load_history(samples, replace=True)
        m.fit(samples[-1].ts)
        return m

    def rows(m, now):
        return len(build_dataset(m._grid, m.config, now.timestamp()).k)

    clean_model = fitted(run.samples)
    clean = clean_model.parameters()
    spiked = list(run.samples)
    n_spikes = 0
    for n, i in enumerate(range(600, len(spiked), 1200)):  # one spike every 20 h
        n_spikes += 1
        spiked[i] = replace(spiked[i], indoor_c=spiked[i].indoor_c + (4.0 if n % 2 else -4.0))
    spiked_model = fitted(spiked)
    guarded = spiked_model.parameters()
    assert guarded.heating_rate_k_per_h == pytest.approx(clean.heating_rate_k_per_h, rel=0.03)
    assert guarded.loss_rate_per_h == pytest.approx(clean.loss_rate_per_h, rel=0.03)
    assert guarded.gains_k_per_h == pytest.approx(clean.gains_k_per_h, abs=0.05)
    assert _rel(guarded.heating_rate_k_per_h, spec.a) < 0.15
    assert _rel(guarded.loss_rate_per_h, spec.b) < 0.15
    # the guard dropped the rows around the spikes (each spike touches two grid steps)
    now = run.samples[-1].ts
    assert rows(clean_model, now) - rows(spiked_model, now) >= n_spikes


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
    assert s["status"] == "ok"
    assert s["mode"] == "outdoor"
    assert set(s["criterion"]) == {str(m) for m in range(0, 91, 5)}
    assert "cooling_tau_h" in s["cross_check"]
    assert "tangent_dead_time_min" in s["cross_check"]


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
    assert cool is not None
    assert 5 < cool.tau_hours < 25
    assert dead is not None
    assert 5 < dead.minutes < 60


def test_solve_linear_and_projection():
    m = [[4.0, 1.0, 0.0], [1.0, 3.0, 1.0], [0.0, 1.0, 2.0]]
    x = solve_linear(m, [1.0, 2.0, 3.0])
    for i in range(3):
        assert sum(m[i][j] * x[j] for j in range(3)) == pytest.approx([1.0, 2.0, 3.0][i])
    assert solve_linear([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]) is None
    # unconstrained optimum has a < 0 and b < 0 → projected and re-solved
    eye = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    theta, proj = solve_projected(eye, [-1.0, -2.0, 0.5])
    assert theta == [A_MIN, B_MIN, 0.5]
    assert set(proj) == {"a", "b"}


def _idle_room_with_bursts() -> tuple[Dataset, list[float | None]]:
    """A mostly idle room: 5 % of the rows heat, the rest do not move at all.

    Mean heating rate a=1.8 K/h, seen through a sensor with 0.1 K resolution: one step reads
    0.1 K (1.2 K/h on a 5 minute grid) and the next 0.2 K (2.4 K/h).
    """
    cfg = ModelConfig()
    n = 1200
    dt_h = cfg.step_minutes / 60.0
    effort: list[float | None] = [1.0 if (k % 120) < 6 else 0.0 for k in range(n + 1)]
    ys = [(1.2 if k % 2 else 2.4) * (effort[k] or 0.0) for k in range(n)]
    xs = [5.0] * n  # constant lift over outdoor: b and c are not separable, only a matters
    return Dataset(list(range(n)), ys, xs, [1.0] * n, True, dt_h), effort


def test_scale_floor_keeps_heating_rows_from_being_outliers(monkeypatch: pytest.MonkeyPatch):
    """Idle residuals are ~0, so their MAD says "noise is 0.001 K/h": without a floor every
    heating row is an outlier and the fit ignores the heating (on real sensors the heating
    rate came out ten times too small)."""
    ds, effort = _idle_room_with_bursts()
    floored = fit_rc(ds, effort, ModelConfig())
    assert floored is not None
    assert _rel(floored.a, 1.8) < 0.2

    monkeypatch.setattr(fit_module, "SCALE_FLOOR_K", 0.0)
    unfloored = fit_rc(ds, effort, ModelConfig())
    assert unfloored is not None
    # without the floor every heating row is dropped and the estimate is the prior's, not ours
    assert abs(unfloored.a - 1.8) > 3 * abs(floored.a - 1.8)


def test_scale_floor_is_per_step_not_per_hour():
    """The same sensor noise in kelvin is a larger K/h figure on a finer grid."""
    ds, effort = _idle_room_with_bursts()
    res = fit_rc(ds, effort, ModelConfig())
    assert res is not None
    assert res.scale >= fit_module.SCALE_FLOOR_K / ds.dt_h

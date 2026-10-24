"""Closed-form RC solutions checked against a numerical simulation of the same ODE."""

from __future__ import annotations

import math

import pytest

from rtm_core import physics

# dT/dt = a*u(t - d) - b*(T - Tout) + c, T in degC, t in hours.
CASES = [
    # (a K/h, b 1/h, c K/h, tout)
    (3.0, 0.125, 0.0, 5.0),
    (2.0, 0.3, 0.6, -2.0),
    (5.0, 0.8, 1.2, 10.0),
]


def simulate(
    t0: float,
    minutes: float,
    tout: float,
    a: float,
    b: float,
    c: float,
    dead_minutes: float = 0.0,
    effort: float = 0.0,
    dt_s: float = 1.0,
) -> float:
    """Explicit-Euler integration of the ODE with the heat arriving after the dead time."""
    temp = t0
    dt_h = dt_s / 3600.0
    for step in range(round(minutes * 60 / dt_s)):
        elapsed_min = step * dt_s / 60.0
        heat = a * effort if elapsed_min >= dead_minutes else 0.0
        temp += (heat - b * (temp - tout) + c) * dt_h
    return temp


def time_to_reach(
    t0: float,
    target: float,
    tout: float,
    a: float,
    b: float,
    c: float,
    dead_minutes: float,
    effort: float,
    limit_minutes: float = 2000.0,
    dt_s: float = 1.0,
) -> float | None:
    """Minutes until the simulated room first reaches ``target`` (None within the limit)."""
    temp = t0
    dt_h = dt_s / 3600.0
    for step in range(round(limit_minutes * 60 / dt_s)):
        elapsed_min = step * dt_s / 60.0
        if temp >= target:
            return elapsed_min
        heat = a * effort if elapsed_min >= dead_minutes else 0.0
        temp += (heat - b * (temp - tout) + c) * dt_h
    return None


@pytest.mark.parametrize(("a", "b", "c", "tout"), CASES)
@pytest.mark.parametrize("t0", [12.0, 19.0, 27.0])
@pytest.mark.parametrize("minutes", [0.0, 20.0, 180.0])
def test_coast_matches_simulation(a, b, c, tout, t0, minutes):
    closed = physics.coast_temperature(t0, minutes, tout, b, c)
    assert closed == pytest.approx(simulate(t0, minutes, tout, a, b, c), abs=0.01)


def test_coast_limits():
    assert physics.coast_temperature(18.0, 0.0, 5.0, 0.2, 1.0) == 18.0
    # long coast converges to Tout + c/b
    assert physics.coast_temperature(18.0, 1e5, 5.0, 0.2, 1.0) == pytest.approx(10.0)
    # half-life: after ln2/b hours the gap to equilibrium is halved
    half = math.log(2) / 0.2 * 60.0
    assert physics.coast_temperature(20.0, half, 0.0, 0.2, 0.0) == pytest.approx(10.0)


@pytest.mark.parametrize(("a", "b", "c", "tout"), CASES)
def test_equilibrium_is_stationary_point_of_the_ode(a, b, c, tout):
    teq = physics.equilibrium(tout, a, b, c)
    assert a - b * (teq - tout) + c == pytest.approx(0.0)
    assert simulate(teq, 60.0, tout, a, b, c, effort=1.0) == pytest.approx(teq, abs=1e-6)
    assert physics.equilibrium(tout, a, b, c, u=0.5) == pytest.approx(tout + (0.5 * a + c) / b)


def test_hours_to_reach():
    assert physics.hours_to_reach(21.0, 20.0, 25.0, 0.2) == 0.0
    assert physics.hours_to_reach(18.0, 20.0, 20.0, 0.2) is None  # asymptote at the target
    assert physics.hours_to_reach(18.0, 20.0, 19.0, 0.2) is None
    # relaxing towards 25 degC with b = 0.2/h: same trajectory as coasting with Tout + c/b = 25
    hours = physics.hours_to_reach(18.0, 20.0, 25.0, 0.2)
    assert physics.coast_temperature(18.0, hours * 60.0, 25.0, 0.2, 0.0) == pytest.approx(20.0)


@pytest.mark.parametrize(("a", "b", "c", "tout"), CASES)
@pytest.mark.parametrize(("t0", "target"), [(16.0, 20.0), (18.5, 21.0)])
@pytest.mark.parametrize("dead", [0.0, 15.0, 45.0])
def test_preheat_minutes_matches_simulation(a, b, c, tout, t0, target, dead):
    minutes, info = physics.preheat_minutes(t0, target, tout, a, b, c, dead)
    truth = time_to_reach(t0, target, tout, a, b, c, dead, effort=1.0)
    if minutes is None:
        assert info["unreachable"] and truth is None
        return
    assert truth is not None
    assert minutes == pytest.approx(truth, abs=0.1)
    assert info["time_constant_h"] == pytest.approx(1.0 / b)
    assert info["t_infinity_c"] == pytest.approx(physics.equilibrium(tout, a, b, c))
    # The naive d + tau*ln(...) ignores that the room coasts during the dead time.
    drifts_down = physics.coast_temperature(t0, dead, tout, b, c) < t0
    if dead > 0:
        assert (minutes > info["minutes_simple"]) == drifts_down


def test_preheat_room_drifts_up_to_target_during_dead_time():
    """Free equilibrium above the target: the room gets there before the heat arrives."""
    a, b, tout = 3.0, 0.2, 5.0
    c = b * 16.0  # free equilibrium 21 degC
    t0, target, dead = 19.0, 20.5, 600.0
    minutes, info = physics.preheat_minutes(t0, target, tout, a, b, c, dead)
    assert info["start_after_dead_time_c"] >= target
    truth = time_to_reach(t0, target, tout, a, b, c, dead, effort=1.0)
    assert minutes == pytest.approx(truth, abs=0.1)
    assert minutes < dead  # reaches the target by itself, before the heat arrives
    assert minutes < info["minutes_simple"]


def test_preheat_already_at_target_and_unreachable():
    minutes, info = physics.preheat_minutes(21.0, 20.0, 5.0, 3.0, 0.2, 0.0, 30.0)
    assert minutes == 0.0 and info["already_at_target"]
    # full-effort equilibrium is 5 + 1/0.2 = 10 degC: 20 is out of reach
    minutes, info = physics.preheat_minutes(15.0, 20.0, 5.0, 1.0, 0.2, 0.0, 30.0)
    assert minutes is None and info["unreachable"]
    # an equilibrium within the margin of the target counts as unreachable too
    teq = physics.equilibrium(5.0, 3.0, 0.2, 0.0)
    target = teq - physics.REACH_MARGIN_K / 2
    minutes, _ = physics.preheat_minutes(15.0, target, 5.0, 3.0, 0.2, 0.0, 0.0)
    assert minutes is None

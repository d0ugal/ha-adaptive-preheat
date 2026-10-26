"""Run-level duration rule: learn the minutes of a heat-up directly from finished runs.

    minutes = deadtime + mass * gap + loss * lift

``gap`` is how far the room has to rise (target - start, K) and ``lift`` how far the target
sits above outdoor (target - outdoor, K). It is the shape Ecronika/ha-preheat (MIT License,
Copyright (c) 2025 Ecronika, https://github.com/Ecronika/ha-preheat) learns; this is a
re-implementation of the idea, used here as a BASELINE for the RC model in the back-test.

Because it learns from whole runs it never differences a sensor, so the quantisation noise
that hurts a step by step fit cannot reach it. What it cannot do is use the cooling of the
room between runs, react to the effort, or say anything about unreachable targets.

Two learners share the same rule:

* ``update_online``: one gradient step per finished run, with the same guards as the
  original (ignore tiny gaps, relative and absolute step limits, no jump over 20 %);
* ``fit_least_squares``: ridge-regularised least squares over all runs seen so far, pulled
  toward the defaults worth ``PRIOR_RUNS`` runs.

Pure standard library, no Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .fit import solve_linear

DEFAULT_DEADTIME = 15.0  # minutes
DEFAULT_MASS = 20.0  # minutes per K of rise
DEFAULT_LOSS = 5.0  # minutes per K of lift above outdoor
MIN_GAP_K = 0.3  # smaller rises are noise, not a run to learn from
LEARNING_RATE = 0.1
MAX_JUMP = 0.2  # no parameter moves more than 20 % in one update
STEP_REL_LIMIT = 0.3
MASS_BOUNDS = (1.0, 120.0)
LOSS_BOUNDS = (0.0, 50.0)
DEADTIME_BOUNDS = (0.0, 120.0)
PRIOR_RUNS = 3.0  # the defaults count as this many runs in the least squares


@dataclass(frozen=True)
class RunSample:
    """One finished run: how far it rose, the lift of the target over outdoor, minutes taken."""

    gap_k: float
    lift_k: float
    minutes: float


@dataclass(frozen=True)
class RuleModel:
    deadtime: float = DEFAULT_DEADTIME
    mass: float = DEFAULT_MASS
    loss: float = DEFAULT_LOSS
    runs: int = 0

    def predict(self, gap_k: float, lift_k: float) -> float:
        """Minutes to rise ``gap_k`` with the target ``lift_k`` above outdoor (0 if no rise)."""
        if gap_k <= 0.0:
            return 0.0
        return max(0.0, self.deadtime + self.mass * gap_k + self.loss * max(lift_k, 0.0))

    def update_online(self, run: RunSample) -> RuleModel:
        """One gradient step on a finished run (the original's weighting and guards)."""
        if run.gap_k < MIN_GAP_K:
            return self
        error = run.minutes - self.predict(run.gap_k, run.lift_k)
        lift = max(run.lift_k, 0.0)
        rate = LEARNING_RATE * (0.2 if run.gap_k < 0.8 else 1.0)
        mass_term = self.mass * run.gap_k
        loss_term = self.loss * lift
        total = mass_term + loss_term + 0.001
        mass = _step(self.mass, rate * error * (mass_term / total) / run.gap_k, 5.0)
        loss = self.loss
        if lift > 0.5:
            loss = _step(self.loss, rate * error * (loss_term / total) / lift, 2.0)
        return replace(
            self,
            mass=_clamp(mass, *MASS_BOUNDS),
            loss=_clamp(loss, *LOSS_BOUNDS),
            runs=self.runs + 1,
        )


def fit_least_squares(runs: Sequence[RunSample], prior: RuleModel | None = None) -> RuleModel:
    """Ridge least squares of minutes on (1, gap, lift), pulled toward ``prior``."""
    base = prior or RuleModel()
    usable = [r for r in runs if r.gap_k >= MIN_GAP_K]
    if not usable:
        return base
    theta0 = [base.deadtime, base.mass, base.loss]
    rows = [[1.0, r.gap_k, max(r.lift_k, 0.0)] for r in usable]
    # scale each prior pseudo-run to the spread of its feature so it is not swamped
    scales = [1.0, _mean_square([r[1] for r in rows]), _mean_square([r[2] for r in rows])]
    m = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    v = [sum(r[i] * u.minutes for r, u in zip(rows, usable, strict=True)) for i in range(3)]
    for i in range(3):
        m[i][i] += PRIOR_RUNS * scales[i]
        v[i] += PRIOR_RUNS * scales[i] * theta0[i]
    sol = solve_linear(m, v)
    if sol is None:
        return base
    return RuleModel(
        deadtime=_clamp(sol[0], *DEADTIME_BOUNDS),
        mass=_clamp(sol[1], *MASS_BOUNDS),
        loss=_clamp(sol[2], *LOSS_BOUNDS),
        runs=len(usable),
    )


def _mean_square(values: Sequence[float]) -> float:
    return max(sum(v * v for v in values) / len(values), 1e-6)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _step(current: float, delta: float, absolute: float) -> float:
    """Apply ``delta`` limited relatively, absolutely and to a 20 % jump."""
    bound = min(max(abs(current), 1.0) * STEP_REL_LIMIT, absolute)
    new = current + _clamp(delta, -bound, bound)
    if current and abs(new - current) / abs(current) > MAX_JUMP:
        new = current + (MAX_JUMP * abs(current)) * (1.0 if new > current else -1.0)
    return new

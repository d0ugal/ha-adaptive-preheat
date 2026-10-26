"""The run-level duration rule used as a back-test baseline."""

from __future__ import annotations

import pytest

from rtm_core.rule_model import (
    DEFAULT_DEADTIME,
    DEFAULT_LOSS,
    DEFAULT_MASS,
    MASS_BOUNDS,
    MIN_GAP_K,
    RuleModel,
    RunSample,
    fit_least_squares,
)

TRUE = RuleModel(deadtime=10.0, mass=30.0, loss=2.0)


def runs(n: int) -> list[RunSample]:
    out: list[RunSample] = []
    for i in range(n):
        gap = 0.5 + (i * 7 % 13) * 0.3  # spread so the three terms are separable
        lift = 2.0 + (i * 5 % 17)
        out.append(RunSample(gap, lift, TRUE.predict(gap, lift)))
    return out


def test_predict_follows_the_formula_and_is_zero_without_a_rise() -> None:
    model = RuleModel()
    assert model.predict(2.0, 10.0) == pytest.approx(
        DEFAULT_DEADTIME + DEFAULT_MASS * 2.0 + DEFAULT_LOSS * 10.0
    )
    assert model.predict(0.0, 10.0) == 0.0
    assert model.predict(-1.0, 10.0) == 0.0
    assert model.predict(1.0, -5.0) == model.predict(1.0, 0.0)  # a warm day adds nothing


def test_online_update_moves_toward_the_run_and_ignores_noise_gaps() -> None:
    model = RuleModel()
    slower = model.update_online(RunSample(2.0, 10.0, model.predict(2.0, 10.0) + 30.0))
    assert slower.mass > model.mass
    assert slower.runs == 1
    faster = model.update_online(RunSample(2.0, 10.0, 10.0))
    assert faster.mass < model.mass
    assert model.update_online(RunSample(MIN_GAP_K - 0.01, 10.0, 99.0)) is model


def test_online_update_never_jumps_more_than_twenty_percent() -> None:
    model = RuleModel(mass=20.0, loss=5.0)
    after = model.update_online(RunSample(2.0, 10.0, 10_000.0))
    assert after.mass <= model.mass * 1.2 + 1e-9
    assert after.loss <= model.loss * 1.2 + 1e-9
    low, high = MASS_BOUNDS
    assert low <= after.mass <= high


def test_online_update_skips_the_loss_term_without_outdoor_lift() -> None:
    model = RuleModel()
    after = model.update_online(RunSample(2.0, 0.2, 200.0))
    assert after.loss == model.loss


def test_least_squares_recovers_the_rule_from_enough_runs() -> None:
    fitted = fit_least_squares(runs(200))
    assert fitted.mass == pytest.approx(TRUE.mass, rel=0.1)
    assert fitted.loss == pytest.approx(TRUE.loss, rel=0.25)
    assert fitted.deadtime == pytest.approx(TRUE.deadtime, abs=4.0)
    assert fitted.runs == 200
    # the prior fades with the evidence: more runs, closer to the truth
    assert abs(fitted.mass - TRUE.mass) < abs(fit_least_squares(runs(10)).mass - TRUE.mass)


def test_least_squares_stays_near_the_prior_with_one_run() -> None:
    prior = RuleModel()
    fitted = fit_least_squares(runs(1), prior)
    assert abs(fitted.mass - prior.mass) < abs(TRUE.mass - prior.mass)
    assert fit_least_squares([], prior) is prior
    assert fit_least_squares([RunSample(MIN_GAP_K - 0.1, 5.0, 40.0)], prior) is prior

"""Fit trust: each check, its threshold, and real fits of synthetic rooms."""

from __future__ import annotations

from dataclasses import replace

import pytest

from rtm_core import ModelConfig, RCThermalModel
from rtm_core import synthetic as syn
from rtm_core.fit import A_MIN, B_MAX, B_MIN
from rtm_core.trust import (
    DESIGN_OUTDOOR_C,
    MAX_LIFT_FACTOR,
    MIN_HEATING_HOURS,
    MIN_SKILL,
    FitFacts,
    assess_fit,
    max_lift_k,
)

GOOD = FitFacts(
    a=3.0,
    b=0.1,
    c=0.2,
    dead_time_minutes=20.0,
    max_dead_time_minutes=90.0,
    step_minutes=5.0,
    heating_hours=40.0,
    rmse=0.3,
    baseline_rmse=0.9,
    episodes=5,
)


def reasons(**kw: float | int | None) -> tuple[str, ...]:
    return assess_fit(replace(GOOD, **kw), 20.5).reasons


def test_good_fit_is_trusted() -> None:
    verdict = assess_fit(GOOD, 20.5)
    assert verdict.trusted
    assert verdict.reasons == ()


@pytest.mark.parametrize("b", [B_MIN, B_MIN * 1.04, B_MAX, B_MAX * 0.96])
def test_loss_rate_at_a_bound(b: float) -> None:
    assert "loss_rate_at_bound" in reasons(b=b, a=3.0 * b / 0.1, c=0.0)


def test_loss_rate_just_inside_the_bounds_is_fine() -> None:
    assert "loss_rate_at_bound" not in reasons(b=B_MIN * 1.2, a=1.0, c=0.0)
    assert "loss_rate_at_bound" not in reasons(b=B_MAX * 0.9, a=1.0, c=0.0)


@pytest.mark.parametrize("a", [A_MIN, 0.3, 0.49])
def test_heating_rate_low(a: float) -> None:
    assert "heating_rate_low" in reasons(a=a, c=0.0)


def test_heating_rate_at_the_minimum_is_fine() -> None:
    assert "heating_rate_low" not in reasons(a=0.5)


def test_heating_rate_unlearnt_needs_prior_value_and_thin_data() -> None:
    assert "heating_rate_unlearnt" in reasons(a=3.05, heating_hours=4.0)
    assert "heating_rate_unlearnt" not in reasons(a=3.05, heating_hours=40.0)
    assert "heating_rate_unlearnt" not in reasons(a=2.0, heating_hours=4.0)


@pytest.mark.parametrize("d", [0.0, 85.0, 90.0])
def test_dead_time_at_the_edge(d: float) -> None:
    assert reasons(dead_time_minutes=d) == ("dead_time_at_bound",)


@pytest.mark.parametrize("d", [5.0, 30.0, 80.0])
def test_dead_time_inside_the_range_is_fine(d: float) -> None:
    assert reasons(dead_time_minutes=d) == ()


def test_max_reachable_limit_derives_from_target_and_design_temperature() -> None:
    assert max_lift_k(20.5) == pytest.approx(MAX_LIFT_FACTOR * (20.5 - DESIGN_OUTDOOR_C))
    assert max_lift_k(-10.0) == 0.0
    # the same fit is believable for a warm target and not for a cold one
    limit = max_lift_k(20.5)
    facts = replace(GOOD, a=limit * 0.1 - 0.2, b=0.1, c=0.2)
    assert assess_fit(facts, 20.5).trusted
    over = replace(GOOD, a=limit * 0.1 + 0.1, b=0.1, c=0.2)
    assert assess_fit(over, 20.5).reasons == ("max_reachable_implausible",)
    assert "max_reachable_implausible" in assess_fit(facts, 10.0).reasons


def test_max_reachable_below_the_target_is_not_junk() -> None:
    assert assess_fit(replace(GOOD, a=0.6, b=0.1, c=0.0), 20.5).trusted


def test_too_little_heating() -> None:
    assert reasons(heating_hours=MIN_HEATING_HOURS - 0.1, a=2.0) == ("too_little_heating",)
    assert reasons(heating_hours=MIN_HEATING_HOURS, a=2.0) == ()


def test_not_evaluated() -> None:
    assert reasons(rmse=None) == ("not_evaluated",)
    assert reasons(baseline_rmse=None) == ("not_evaluated",)
    assert reasons(baseline_rmse=0.0) == ("not_evaluated",)


def test_no_skill_only_when_the_holdout_had_heating() -> None:
    base = 1.0
    assert reasons(rmse=base, baseline_rmse=base) == ("no_skill",)
    assert reasons(rmse=(1 - MIN_SKILL) * base + 0.001, baseline_rmse=base) == ("no_skill",)
    assert reasons(rmse=(1 - MIN_SKILL) * base - 0.001, baseline_rmse=base) == ()
    assert reasons(rmse=base, baseline_rmse=base, episodes=0) == ()


def test_reasons_accumulate() -> None:
    junk = replace(GOOD, a=0.05, b=B_MIN, dead_time_minutes=0.0, heating_hours=0.0)
    verdict = assess_fit(junk, 20.5)
    assert not verdict.trusted
    assert {
        "loss_rate_at_bound",
        "heating_rate_low",
        "dead_time_at_bound",
        "too_little_heating",
    } <= set(verdict.reasons)


# ---------------------------------------------------------------- real fits


def _model(spec: syn.RoomSpec, days: float) -> tuple[RCThermalModel, syn.SyntheticRun]:
    run = syn.generate(spec, days)
    model = RCThermalModel(ModelConfig())
    model.load_history(run.samples, replace=True)
    model.fit(run.samples[-1].ts)
    return model, run


@pytest.mark.parametrize("room", ["radiator", "fast_radiator", "underfloor", "no_outdoor"])
def test_synthetic_rooms_with_two_weeks_are_trusted(room: str) -> None:
    model, run = _model(syn.ROOMS[room](), 14)
    verdict = model.trust(20.5)
    assert verdict is not None
    assert verdict.trusted, verdict.reasons
    est = model.preheat(run.samples[-1].ts, 18.0, 20.5, 5.0)
    assert est.trusted is True
    assert est.trust_reasons == ()


def test_no_trust_before_the_first_fit() -> None:
    model = RCThermalModel(ModelConfig())
    assert model.trust(20.5) is None


def test_idle_room_is_untrusted_and_the_estimate_carries_the_reasons() -> None:
    spec = syn.radiator_room(eco_c=15.0, comfort_c=15.0, front_amp=0.5)
    run = syn.generate(spec, 0.75, initial_temp=20.0, windows=False, disturbances=False)
    model = RCThermalModel(ModelConfig())
    model.load_history(run.samples, replace=True)
    model.fit(run.samples[-1].ts)
    verdict = model.trust(20.5)
    assert verdict is not None
    assert not verdict.trusted
    assert "too_little_heating" in verdict.reasons
    est = model.preheat(run.samples[-1].ts, 18.0, 20.5, 5.0)
    assert est.trusted is False
    assert "too_little_heating" in est.trust_reasons


def test_trust_survives_a_state_round_trip() -> None:
    model, _ = _model(syn.radiator_room(), 14)
    restored = RCThermalModel.from_dict(model.to_dict(), ModelConfig())
    assert restored.trust(20.5) == model.trust(20.5)

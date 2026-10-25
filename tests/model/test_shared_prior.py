"""Prior borrowed from the mature rooms of a boiler zone."""

from __future__ import annotations

import math

import pytest

from rtm_core import GroupPrior, ModelConfig, PeerSummary, RCThermalModel, build_group_prior
from rtm_core import synthetic as syn
from rtm_core.fit import PRIOR_HOURS, Prior
from rtm_core.shared_prior import MAX_PRIOR_HOURS, MIN_PEER_MATURITY, is_usable_peer


def peer(name: str, a: float = 3.0, b: float = 0.2, maturity: float = 0.8) -> PeerSummary:
    return PeerSummary(name, a, b, maturity)


def test_no_peers_gives_no_prior() -> None:
    assert build_group_prior([]) is None


def test_immature_peers_are_ignored() -> None:
    peers = [peer("a", maturity=MIN_PEER_MATURITY - 0.01), peer("b", maturity=0.0)]
    assert build_group_prior(peers) is None


def test_peer_at_the_threshold_counts() -> None:
    prior = build_group_prior([peer("a", maturity=MIN_PEER_MATURITY)])
    assert prior is not None
    assert prior.peers == ("a",)


def test_one_peer_lends_its_parameters_with_half_weight() -> None:
    prior = build_group_prior([peer("a", a=2.5, b=0.3)])
    assert prior == GroupPrior(a=2.5, b=0.3, hours=12.0, peers=("a",))


def test_median_of_peers_and_capped_weight() -> None:
    peers = [peer("c", a=1.0, b=0.1), peer("a", a=4.0, b=0.4), peer("b", a=2.0, b=0.2)]
    prior = build_group_prior(peers)
    assert prior is not None
    assert prior.a == 2.0
    assert prior.b == pytest.approx(0.2)
    assert prior.hours == MAX_PRIOR_HOURS
    assert prior.peers == ("a", "b", "c")  # sorted, so diagnostics are stable


def test_even_number_of_peers_averages_the_middle() -> None:
    prior = build_group_prior([peer("a", b=0.1), peer("b", b=0.3)])
    assert prior is not None
    assert prior.b == pytest.approx(0.2)


def test_one_mature_peer_among_immature_ones() -> None:
    prior = build_group_prior([peer("a", maturity=0.1), peer("b", a=2.0)])
    assert prior is not None
    assert prior.peers == ("b",)


@pytest.mark.parametrize(
    ("a", "b"),
    [(math.nan, 0.2), (3.0, math.inf), (0.0, 0.2), (3.0, -0.1)],
)
def test_nonsensical_peers_are_unusable(a: float, b: float) -> None:
    assert not is_usable_peer(peer("x", a=a, b=b))


def test_prior_conversion_and_description() -> None:
    prior = build_group_prior([peer("a", a=2.5, b=0.3)])
    assert prior is not None
    assert prior.as_prior() == Prior(a=2.5, b=0.3, c=0.0, hours=12.0)
    assert prior.as_dict() == {
        "source": "group",
        "a": 2.5,
        "b": 0.3,
        "hours": 12.0,
        "peers": ["a"],
    }


def _fitted(prior: GroupPrior | None, days: float = 1.5) -> RCThermalModel:
    run = syn.generate(syn.ROOMS["radiator"](), days, windows=False)
    model = RCThermalModel(ModelConfig())
    model.set_prior(prior)
    model.load_history(run.samples, replace=True)
    model.fit(run.samples[-1].ts)
    return model


def test_default_fit_records_the_default_prior() -> None:
    summary = _fitted(None).last_fit_summary()
    assert summary["prior"] == {"source": "default", "peers": []}


def test_group_prior_is_recorded_and_pulls_a_data_poor_fit() -> None:
    spec = syn.ROOMS["radiator"]()
    prior = GroupPrior(a=spec.a, b=spec.b, hours=48.0, peers=("a", "b"))
    default = _fitted(None).parameters()
    shared = _fitted(prior)
    assert shared.last_fit_summary()["prior"]["source"] == "group"
    assert shared.last_fit_summary()["prior"]["peers"] == ["a", "b"]
    p = shared.parameters()
    assert default is not None
    assert p is not None
    # starting from the right answer can only help against the generic defaults
    err_default = abs(default.loss_rate_per_h - spec.b) + abs(default.heating_rate_k_per_h - spec.a)
    err_shared = abs(p.loss_rate_per_h - spec.b) + abs(p.heating_rate_k_per_h - spec.a)
    assert err_shared <= err_default + 1e-9


def test_prior_can_be_cleared_again() -> None:
    model = _fitted(GroupPrior(a=2.0, b=0.2, hours=12.0, peers=("a",)))
    model.set_prior(None)
    run = syn.generate(syn.ROOMS["radiator"](), 1.5, windows=False)
    model.fit(run.samples[-1].ts)
    assert model.last_fit_summary()["prior"]["source"] == "default"


def test_default_prior_weight_unchanged() -> None:
    assert Prior().hours == PRIOR_HOURS

"""Zone groups: zones on one boiler zone share a prior while their own fit is immature."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_preheat.const import (
    CONF_CLIMATE,
    CONF_HALF_LIFE_DAYS,
    CONF_HISTORY_DAYS,
    CONF_TARGET_MODE,
    CONF_TARGET_TEMPERATURE,
    CONF_UPDATE_INTERVAL_MINUTES,
    CONF_ZONE_GROUP,
    CONF_ZONE_NAME,
    DOMAIN,
)
from custom_components.adaptive_preheat.coordinator import RTMCoordinator
from custom_components.adaptive_preheat.diagnostics import async_get_config_entry_diagnostics
from custom_components.adaptive_preheat.rtm_core.shared_prior import GroupPrior
from custom_components.adaptive_preheat.rtm_core.synthetic import generate, radiator_room
from custom_components.adaptive_preheat.zone_group import DATA_KEY

from .conftest import setup_entry


async def add_zone(
    hass: HomeAssistant, name: str, group: str | None, maturity: float = 0.9
) -> MockConfigEntry:
    """Set up a zone (fake model with the given maturity) in ``group``."""
    climate = f"climate.{name}"
    hass.states.async_set(climate, "heat", {"current_temperature": 19.0, "temperature": 21.0})
    options: dict[str, Any] = {CONF_TARGET_MODE: "fixed", CONF_TARGET_TEMPERATURE: 20.5}
    if group is not None:
        options[CONF_ZONE_GROUP] = group
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=climate,
        data={CONF_ZONE_NAME: name, CONF_CLIMATE: climate},
        options=options,
    )
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)
    entry.runtime_data.model.maturity = maturity
    return entry


async def fit(hass: HomeAssistant, entry: MockConfigEntry) -> RTMCoordinator:
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    return coordinator


def maturity_attrs(hass: HomeAssistant, name: str) -> dict[str, Any]:
    return dict(hass.states.get(f"sensor.{name}_model_maturity").attributes)


async def test_immature_zone_borrows_from_a_mature_peer(
    hass: HomeAssistant, fake_model: None
) -> None:
    peer = await add_zone(hass, "bedroom_1", "Upstairs", maturity=0.9)
    target = await add_zone(hass, "bedroom_2", " upstairs ", maturity=0.1)
    await fit(hass, peer)
    coordinator = await fit(hass, target)
    prior = coordinator.model.priors_seen[-1]
    assert isinstance(prior, GroupPrior)
    assert prior.peers == ("bedroom_1",)
    assert (prior.a, prior.b) == (3.0, 0.125)  # the peer's fitted values
    attrs = maturity_attrs(hass, "bedroom_2")
    assert attrs["zone_group"] == "upstairs"
    assert attrs["prior_source"] == "group"
    assert attrs["prior_peers"] == ["bedroom_1"]


async def test_mature_zone_keeps_its_own_data(hass: HomeAssistant, fake_model: None) -> None:
    peer = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    other = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.8)
    await fit(hass, peer)
    await fit(hass, other)
    coordinator = await fit(hass, peer)
    assert coordinator.model.priors_seen == [None, None]
    assert maturity_attrs(hass, "bedroom_1")["prior_source"] == "default"


async def test_no_peers_and_immature_peers_use_the_default(
    hass: HomeAssistant, fake_model: None
) -> None:
    lone = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.1)
    assert (await fit(hass, lone)).model.priors_seen == [None]
    weak = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.2)
    await fit(hass, weak)
    assert (await fit(hass, lone)).model.priors_seen == [None, None]
    assert maturity_attrs(hass, "bedroom_1")["prior_source"] == "default"


async def test_zones_without_a_group_or_in_another_group_do_not_share(
    hass: HomeAssistant, fake_model: None
) -> None:
    mature = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    await fit(hass, mature)
    ungrouped = await add_zone(hass, "living_room", None, maturity=0.1)
    elsewhere = await add_zone(hass, "study", "downstairs", maturity=0.1)
    assert (await fit(hass, ungrouped)).model.priors_seen == [None]
    assert (await fit(hass, elsewhere)).model.priors_seen == [None]
    assert maturity_attrs(hass, "living_room")["zone_group"] is None
    assert hass.data[DOMAIN][DATA_KEY].members.keys() == {"upstairs", "downstairs"}


async def test_unloaded_peer_is_forgotten_and_registry_cleaned(
    hass: HomeAssistant, fake_model: None
) -> None:
    peer = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    target = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.1)
    await fit(hass, peer)
    assert isinstance((await fit(hass, target)).model.priors_seen[-1], GroupPrior)
    assert await hass.config_entries.async_unload(peer.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (await fit(hass, target)).model.priors_seen[-1] is None
    assert await hass.config_entries.async_unload(target.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert DOMAIN not in hass.data


async def test_prior_is_an_immutable_copy_when_a_peer_unloads_during_the_fit(
    hass: HomeAssistant, fake_model: None
) -> None:
    peer = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    target = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.1)
    await fit(hass, peer)
    model = target.runtime_data.model
    real_fit = model.fit

    def fit_while_peer_unloads(now: Any) -> dict[str, Any]:
        # runs in the executor: the registry may change under it, the prior must not
        hass.loop.call_soon_threadsafe(hass.data[DOMAIN][DATA_KEY].leave, peer.entry_id)
        return real_fit(now)

    model.fit = fit_while_peer_unloads  # type: ignore[method-assign]
    await fit(hass, target)
    prior = model.priors_seen[-1]
    assert isinstance(prior, GroupPrior)
    assert prior.peers == ("bedroom_1",)
    try:
        prior.a = 1.0  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("prior must be frozen")


async def test_reloaded_peer_is_available_again(hass: HomeAssistant, fake_model: None) -> None:
    peer = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    target = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.1)
    await fit(hass, peer)
    assert await hass.config_entries.async_reload(peer.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    # the restored model still has its parameters; the reloaded zone published them again
    reloaded = peer.runtime_data
    reloaded.model.maturity = 0.9
    await fit(hass, peer)
    coordinator = await fit(hass, target)
    prior = coordinator.model.priors_seen[-1]
    assert isinstance(prior, GroupPrior)
    assert prior.peers == ("bedroom_1",)


async def test_reset_withdraws_the_zone_from_its_group(
    hass: HomeAssistant, fake_model: None
) -> None:
    peer = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    target = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.1)
    await fit(hass, peer)
    await peer.runtime_data.async_reset()
    assert (await fit(hass, target)).model.priors_seen[-1] is None


async def test_diagnostics_show_the_prior(hass: HomeAssistant, fake_model: None) -> None:
    peer = await add_zone(hass, "bedroom_1", "upstairs", maturity=0.9)
    target = await add_zone(hass, "bedroom_2", "upstairs", maturity=0.1)
    await fit(hass, peer)
    await fit(hass, target)
    diag = await async_get_config_entry_diagnostics(hass, target)
    assert diag["zone_group"] == "upstairs"
    assert diag["prior"]["source"] == "group"
    assert diag["prior"]["peers"] == ["bedroom_1"]
    json.dumps(diag, default=str)


async def test_real_models_share_a_prior(hass: HomeAssistant, no_history: Any) -> None:
    """Real RC models: the data-poor zone records the group prior in its fit summary."""
    now = dt_util.utcnow()

    def runs() -> tuple[Any, Any]:
        spec = radiator_room()
        return (
            generate(spec, days=12, start=now - timedelta(days=12), sample_minutes=5).samples,
            generate(spec, days=1.2, start=now - timedelta(days=1.2), sample_minutes=5).samples,
        )

    long_run, short_run = await hass.async_add_executor_job(runs)

    async def history(_hass: Any, inputs: Any, _days: Any) -> Any:
        return long_run if inputs.climate == "climate.bedroom_1" else short_run

    no_history.side_effect = history
    peer = await add_zone_real(hass, "bedroom_1")
    peer_coordinator: RTMCoordinator = peer.runtime_data
    assert peer_coordinator.model.quality().maturity >= 0.3
    target = await add_zone_real(hass, "bedroom_2")
    # the bootstrap fit may have happened before the peer published: refit explicitly
    coordinator: RTMCoordinator = target.runtime_data
    coordinator._dirty = True
    await coordinator._async_fit_if_due(dt_util.utcnow(), force=True)
    summary = coordinator.last_fit_summary
    assert summary is not None
    assert summary["prior"]["source"] == "group"
    assert summary["prior"]["peers"] == ["bedroom_1"]
    assert coordinator.model.parameters() is not None
    json.dumps(await async_get_config_entry_diagnostics(hass, target), default=str)


async def add_zone_real(hass: HomeAssistant, name: str) -> MockConfigEntry:
    climate = f"climate.{name}"
    hass.states.async_set(climate, "heat", {"current_temperature": 19.0, "temperature": 21.0})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=climate,
        data={CONF_ZONE_NAME: name, CONF_CLIMATE: climate},
        options={
            CONF_TARGET_MODE: "fixed",
            CONF_TARGET_TEMPERATURE: 20.5,
            CONF_ZONE_GROUP: "upstairs",
            CONF_HALF_LIFE_DAYS: 21,
            CONF_HISTORY_DAYS: 30,
            CONF_UPDATE_INTERVAL_MINUTES: 5,
        },
    )
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)
    return entry

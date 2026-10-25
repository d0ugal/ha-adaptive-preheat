"""Run scoring through the coordinator and its sensors."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from custom_components.room_thermal_model.coordinator import RTMCoordinator

from .conftest import CLIMATE, SLUG, climate_attrs, make_entry, set_inputs, setup_entry

LAST_ERROR = f"sensor.{SLUG}_last_run_error"
RUN_MAE = f"sensor.{SLUG}_run_mae"


def _room(hass: HomeAssistant, current: float, action: str = "heating", target: float = 21.0):
    hass.states.async_set(
        CLIMATE, "heat", climate_attrs(current=current, target=target, action=action)
    )


async def _fitted(hass: HomeAssistant, entry) -> RTMCoordinator:
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    return coordinator


async def _tick(hass: HomeAssistant, coordinator: RTMCoordinator, freezer, minutes: int) -> None:
    freezer.tick(timedelta(minutes=minutes))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_unknown_before_any_run(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    await setup_entry(hass, make_entry(hass))
    assert hass.states.get(LAST_ERROR).state == STATE_UNKNOWN
    assert hass.states.get(RUN_MAE).state == STATE_UNKNOWN
    assert hass.states.get(RUN_MAE).attributes["runs"] == 0


async def test_run_is_opened_and_scored(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    """Heating from 19 C to the 20.5 C target: predicted 30 min, took 20."""
    _room(hass, 19.0)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator = await _fitted(hass, entry)
    open_run = hass.states.get(RUN_MAE).attributes["open_run"]
    assert open_run["predicted_minutes"] == 30.0
    assert open_run["start_c"] == 19.0
    assert open_run["target_c"] == 20.5

    await _tick(hass, coordinator, freezer, 10)
    _room(hass, 19.8)
    await _tick(hass, coordinator, freezer, 10)
    _room(hass, 20.5)
    await _tick(hass, coordinator, freezer, 0)

    assert float(hass.states.get(LAST_ERROR).state) == 10.0
    assert float(hass.states.get(RUN_MAE).state) == 10.0
    attrs = hass.states.get(RUN_MAE).attributes
    assert attrs["open_run"] is None
    assert attrs["runs"] == attrs["reached"] == attrs["clean_runs"] == 1
    assert attrs["bias_minutes"] == 10.0
    last = attrs["last_run"]
    assert last["outcome"] == "reached"
    assert last["actual_minutes"] == 20.0
    assert last["heating_fraction"] == 1.0
    assert last["clean"] is True


async def test_idle_room_opens_no_run(hass: HomeAssistant, fake_model: None) -> None:
    """The estimate assumes full heating, so an idle zone is not scored."""
    _room(hass, 19.0, action="idle")
    entry = await setup_entry(hass, make_entry(hass))
    await _fitted(hass, entry)
    assert hass.states.get(RUN_MAE).attributes["open_run"] is None


async def test_scores_survive_a_reload(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    _room(hass, 19.0)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator = await _fitted(hass, entry)
    await _tick(hass, coordinator, freezer, 10)
    _room(hass, 20.5)
    await _tick(hass, coordinator, freezer, 10)
    before = dict(hass.states.get(RUN_MAE).attributes["last_run"])
    assert before["outcome"] == "reached"
    assert hass.states.get(RUN_MAE).attributes["runs"] == 1
    stored = coordinator._store_data()["scoring"]
    assert len(stored["results"]) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(RUN_MAE).attributes["runs"] == 1
    assert hass.states.get(RUN_MAE).attributes["last_run"] == before
    assert float(hass.states.get(LAST_ERROR).state) == before["error_minutes"]


async def test_reset_clears_the_scores(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    _room(hass, 19.0)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator = await _fitted(hass, entry)
    await _tick(hass, coordinator, freezer, 10)
    _room(hass, 20.5)
    await _tick(hass, coordinator, freezer, 10)
    assert hass.states.get(RUN_MAE).attributes["runs"] == 1
    await coordinator.async_reset()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(RUN_MAE).attributes["runs"] == 0
    assert hass.states.get(LAST_ERROR).state == STATE_UNKNOWN

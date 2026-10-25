"""An untrusted fit withholds the estimate (unless allowed) but keeps the parameters."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.room_thermal_model.coordinator import RTMCoordinator

from .conftest import (
    ARRIVAL,
    CLIMATE,
    SLUG,
    FakeModel,
    climate_attrs,
    make_entry,
    set_inputs,
    setup_entry,
)

PREHEAT = f"sensor.{SLUG}_preheat_minutes"
START = f"sensor.{SLUG}_preheat_start"
NOW_SENSOR = f"binary_sensor.{SLUG}_preheat_now"
MATURITY = f"sensor.{SLUG}_model_maturity"
RATE = f"sensor.{SLUG}_heating_rate"
MAX_REACHABLE = f"sensor.{SLUG}_max_reachable_temperature"
RUN_MAE = f"sensor.{SLUG}_run_mae"
REASONS = ("loss_rate_at_bound", "too_little_heating")


async def _fit(hass: HomeAssistant, entry, trusted: bool | None) -> RTMCoordinator:
    coordinator: RTMCoordinator = entry.runtime_data
    model: FakeModel = coordinator.model
    model.trusted = trusted
    model.trust_reasons = () if trusted else REASONS
    model.due = True
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    return coordinator


async def test_untrusted_fit_is_unknown_but_parameters_publish(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    await _fit(hass, entry, trusted=False)
    state = hass.states.get(PREHEAT)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["reason"] == "untrusted_fit"
    assert state.attributes["trusted"] is False
    assert state.attributes["trust_reasons"] == list(REASONS)
    maturity = hass.states.get(MATURITY).attributes
    assert maturity["trusted"] is False
    assert maturity["trust_reasons"] == list(REASONS)
    assert float(hass.states.get(RATE).state) == 3.0
    assert float(hass.states.get(MAX_REACHABLE).state) == 24.0


async def test_trusted_fit_publishes_minutes(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    await _fit(hass, entry, trusted=True)
    state = hass.states.get(PREHEAT)
    assert float(state.state) == 30.0
    assert state.attributes["reason"] == "ok"
    assert state.attributes["trusted"] is True
    assert state.attributes["trust_reasons"] == []


async def test_allow_untrusted_publishes_anyway(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass, allow_untrusted=True))
    assert entry.runtime_data.allow_untrusted is True
    await _fit(hass, entry, trusted=False)
    state = hass.states.get(PREHEAT)
    assert float(state.state) == 30.0
    assert state.attributes["reason"] == "ok"
    assert state.attributes["trusted"] is False  # still reported
    assert state.attributes["trust_reasons"] == list(REASONS)


async def test_allow_untrusted_defaults_off(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    assert entry.runtime_data.allow_untrusted is False


async def test_untrusted_means_no_estimate_for_start_now_and_scoring(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    """No start time, preheat_now unknown and the run scorer opens nothing."""
    set_inputs(hass)
    arrival = dt_util.utcnow() + timedelta(hours=2)
    hass.states.async_set(ARRIVAL, arrival.isoformat(), {"device_class": "timestamp"})
    hass.states.async_set(CLIMATE, "heat", climate_attrs(current=19.0, action="heating"))
    entry = await setup_entry(hass, make_entry(hass, arrival=True))
    coordinator = await _fit(hass, entry, trusted=False)
    assert hass.states.get(START).state == STATE_UNKNOWN
    assert coordinator.data.preheat_start is None
    assert coordinator.data.coast_c is not None  # the coast forecast is not an estimate
    assert hass.states.get(NOW_SENSOR).state == STATE_UNKNOWN
    assert hass.states.get(RUN_MAE).attributes["open_run"] is None
    freezer.tick(timedelta(minutes=30))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(NOW_SENSOR).state == STATE_UNKNOWN
    assert hass.states.get(RUN_MAE).attributes["runs"] == 0


async def test_unreachable_untrusted_is_untrusted_not_unreachable(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    entry.runtime_data.model.reachable = False
    await _fit(hass, entry, trusted=False)
    assert hass.states.get(PREHEAT).attributes["reason"] == "untrusted_fit"

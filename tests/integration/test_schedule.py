"""Schedule-aware preheat: the next block of a schedule helper acts as the arrival."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.adaptive_preheat.coordinator import RTMCoordinator

from .conftest import (
    ARRIVAL,
    SCHEDULE,
    SLUG,
    make_entry,
    set_inputs,
    setup_entry,
)

PREHEAT = f"sensor.{SLUG}_preheat_minutes"
START = f"sensor.{SLUG}_preheat_start"
NOW_SENSOR = f"binary_sensor.{SLUG}_preheat_now"
# Monday 2026-03-02 12:00 in the test time zone (US/Pacific, UTC-8)
NOON = "2026-03-02 20:00:00+00:00"


def block(frm: str, to: str, target: Any = 22.0, key: str = "target_temperature") -> dict:
    return {"from": frm, "to": to, "data": {key: target}}


def register_schedule(
    hass: HomeAssistant, days: dict[str, list[dict]] | Callable[[], Any]
) -> list[ServiceCall]:
    """Fake ``schedule.get_schedule``; the returned list records the calls."""
    calls: list[ServiceCall] = []

    async def handler(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        payload = days() if callable(days) else days
        return {SCHEDULE: payload}

    hass.services.async_register(
        "schedule", "get_schedule", handler, supports_response=SupportsResponse.ONLY
    )
    return calls


def set_schedule_state(hass: HomeAssistant, state: str = "off", next_event: str = "x") -> None:
    hass.states.async_set(SCHEDULE, state, {"next_event": next_event})


async def _fitted(hass: HomeAssistant, entry: Any) -> RTMCoordinator:
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    return coordinator


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, **kwargs: Any) -> Any:
    freezer.move_to(NOON)
    set_inputs(hass)
    set_schedule_state(hass)
    return await setup_entry(hass, make_entry(hass, schedule="default", **kwargs))


async def test_entities_exist_with_a_schedule(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    set_schedule_state(hass)
    register_schedule(hass, {})
    entry = await setup_entry(hass, make_entry(hass, schedule="default"))
    registry = er.async_get(hass)
    assert registry.async_get(START).unique_id == f"{entry.entry_id}_preheat_start"
    assert registry.async_get(NOW_SENSOR).unique_id == f"{entry.entry_id}_preheat_now"
    assert hass.states.get(NOW_SENSOR).state == "off"


async def test_next_block_drives_preheat_start_and_now(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    """Block Monday 15:00 at 22 C: the lead uses 22 C, not the room target of 20.5 C."""
    register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22)]})
    entry = await _setup(hass, freezer)
    await _fitted(hass, entry)
    block_start = datetime.fromisoformat("2026-03-02 15:00:00-08:00")
    # FakeModel: 3 K/h, coast -1 K/h; lead = 60 + (180 - lead) / 3 -> 90 min.
    state = hass.states.get(START)
    assert abs(dt_util.parse_datetime(state.state) - (block_start - timedelta(minutes=90))) < (
        timedelta(seconds=10)
    )
    attrs = state.attributes
    assert attrs["arrival_source"] == "schedule"
    assert attrs["arrival_target_c"] == 22.0
    assert dt_util.parse_datetime(attrs["next_block_start"]) == block_start
    assert attrs["next_block_target_c"] == 22.0
    # preheat_minutes keeps its meaning: minutes to the current target of 20.5 C
    minutes = hass.states.get(PREHEAT)
    assert float(minutes.state) == 30.0
    assert minutes.attributes["target_c"] == 20.5
    assert minutes.attributes["next_block_target_c"] == 22.0
    assert hass.states.get(NOW_SENSOR).attributes["arrival_source"] == "schedule"

    # The timer at the preheat start turns preheat_now on...
    start = dt_util.parse_datetime(state.state)
    freezer.move_to(start + timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(NOW_SENSOR).state == "on"
    # ...and at the block start it is off (no later rise within 24 h)
    freezer.move_to(block_start + timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(NOW_SENSOR).state == "off"
    assert hass.states.get(START).state == "unknown"


async def test_timers_cover_preheat_start_and_block_start(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22)]})
    entry = await _setup(hass, freezer)
    coordinator = await _fitted(hass, entry)
    assert len(coordinator._unsub_timers) == 2


async def test_service_is_not_hammered(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    calls = register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22)]})
    entry = await _setup(hass, freezer)
    coordinator = await _fitted(hass, entry)
    before = len(calls)
    assert before >= 1
    assert calls[0].data["entity_id"] == SCHEDULE
    for _ in range(3):
        freezer.tick(timedelta(seconds=30))
        await coordinator.async_refresh()
    assert len(calls) == before
    # a state change of the schedule refetches
    set_schedule_state(hass, "on", "later")
    await hass.async_block_till_done(wait_background_tasks=True)
    await coordinator.async_refresh()
    assert len(calls) == before + 1
    # and so does the passing of the refresh interval
    freezer.tick(timedelta(minutes=6))
    await coordinator.async_refresh()
    assert len(calls) == before + 2


async def test_inside_a_block_the_next_one_is_used(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    register_schedule(
        hass,
        {"monday": [block("11:00:00", "13:00:00", 21), block("18:00:00", "22:00:00", 23)]},
    )
    entry = await _setup(hass, freezer)
    await _fitted(hass, entry)
    attrs = hass.states.get(START).attributes
    assert dt_util.parse_datetime(attrs["next_block_start"]) == datetime.fromisoformat(
        "2026-03-02 18:00:00-08:00"
    )
    assert attrs["next_block_target_c"] == 23.0


async def test_custom_target_key(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22, key="temp")]})
    freezer.move_to(NOON)
    set_inputs(hass)
    set_schedule_state(hass)
    entry = await setup_entry(hass, make_entry(hass, schedule="temp"))
    await _fitted(hass, entry)
    assert hass.states.get(START).attributes["next_block_target_c"] == 22.0


async def test_malformed_schedule_degrades(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    register_schedule(
        hass, {"monday": [{"from": "x", "to": "y"}, block("15:00:00", "17:00:00", "hot")]}
    )
    entry = await _setup(hass, freezer)
    await _fitted(hass, entry)
    assert hass.states.get(START).state == "unknown"
    assert hass.states.get(START).attributes["next_block_start"] is None
    assert float(hass.states.get(PREHEAT).state) == 30.0


async def test_failing_service_logs_once_and_degrades(
    hass: HomeAssistant,
    fake_model: None,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(call: ServiceCall) -> dict[str, Any]:
        raise HomeAssistantError("boom")

    hass.services.async_register(
        "schedule", "get_schedule", broken, supports_response=SupportsResponse.ONLY
    )
    entry = await _setup(hass, freezer)
    coordinator = await _fitted(hass, entry)
    for _ in range(3):
        freezer.tick(timedelta(minutes=6))
        await coordinator.async_refresh()
    assert caplog.text.count("Reading schedule") == 1
    assert hass.states.get(START).state == "unknown"
    assert float(hass.states.get(PREHEAT).state) == 30.0  # today's behaviour is intact
    # recovery re-arms the warning and picks the schedule up
    hass.services.async_remove("schedule", "get_schedule")
    register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22)]})
    freezer.tick(timedelta(minutes=6))
    await coordinator.async_refresh()
    assert hass.states.get(START).attributes["next_block_target_c"] == 22.0


async def test_missing_service_is_handled(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass, freezer)  # no schedule integration loaded
    await _fitted(hass, entry)
    assert hass.states.get(START).state == "unknown"


async def test_unavailable_schedule_has_no_next_block(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    calls = register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22)]})
    entry = await _setup(hass, freezer)
    coordinator = await _fitted(hass, entry)
    hass.states.async_set(SCHEDULE, "unavailable")
    await hass.async_block_till_done(wait_background_tasks=True)
    n = len(calls)
    await coordinator.async_refresh()
    assert len(calls) == n
    assert hass.states.get(START).state == "unknown"
    hass.states.async_remove(SCHEDULE)
    await coordinator.async_refresh()
    assert hass.states.get(START).state == "unknown"


async def test_sooner_of_arrival_sensor_and_schedule_wins(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    register_schedule(hass, {"monday": [block("15:00:00", "17:00:00", 22)]})
    freezer.move_to(NOON)
    set_inputs(hass)
    set_schedule_state(hass)
    sensor_arrival = dt_util.utcnow() + timedelta(hours=2)  # before the 3 h away block
    hass.states.async_set(ARRIVAL, sensor_arrival.isoformat(), {"device_class": "timestamp"})
    entry = await setup_entry(hass, make_entry(hass, arrival=True, schedule="default"))
    await _fitted(hass, entry)
    attrs = hass.states.get(START).attributes
    assert attrs["arrival_source"] == "sensor"
    assert attrs["arrival_target_c"] == 20.5  # the room target for the sensor
    assert attrs["next_block_target_c"] == 22.0  # the schedule is still reported
    # a later sensor arrival: the schedule wins
    hass.states.async_set(
        ARRIVAL, (dt_util.utcnow() + timedelta(hours=5)).isoformat(), {"device_class": "timestamp"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await entry.runtime_data.async_refresh()
    assert hass.states.get(START).attributes["arrival_source"] == "schedule"


async def test_schedule_changes_do_not_create_samples(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    register_schedule(hass, {})
    entry = await _setup(hass, freezer)
    coordinator = await _fitted(hass, entry)
    before = len(coordinator.model.samples)
    set_schedule_state(hass, "on", "again")
    await hass.async_block_till_done(wait_background_tasks=True)
    # the debounced refresh feeds at most one poll sample, never a state-time sample on top
    assert len(coordinator.model.samples) - before <= 1


async def test_without_a_schedule_nothing_changes(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    assert entry.runtime_data.schedule is None
    assert hass.states.get(START) is None

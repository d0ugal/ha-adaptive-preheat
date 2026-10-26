"""Coordinator error, shutdown and edge paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.adaptive_preheat.coordinator import (
    RTMCoordinator,
    parse_arrival,
)
from custom_components.adaptive_preheat.rtm_core.base import PreheatEstimate, Sample

from .conftest import (
    ARRIVAL,
    CLIMATE,
    WEATHER,
    FakeModel,
    make_entry,
    set_inputs,
    setup_entry,
)

NOW = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)


# ---------------------------------------------------------------- parse_arrival


@pytest.mark.parametrize("value", ["unknown", "unavailable", "", "not a date", "2026-13-45"])
def test_parse_arrival_invalid_values(value: str) -> None:
    assert parse_arrival(State(ARRIVAL, value), NOW) is None


def test_parse_arrival_missing_state() -> None:
    assert parse_arrival(None, NOW) is None


def test_parse_arrival_past_and_now_are_not_arrivals() -> None:
    assert parse_arrival(State(ARRIVAL, (NOW - timedelta(minutes=1)).isoformat()), NOW) is None
    assert parse_arrival(State(ARRIVAL, NOW.isoformat()), NOW) is None


def test_parse_arrival_future_is_returned_in_utc() -> None:
    later = (NOW + timedelta(hours=2)).astimezone(dt_util.get_time_zone("Europe/Paris"))
    parsed = parse_arrival(State(ARRIVAL, later.isoformat()), NOW)
    assert parsed == NOW + timedelta(hours=2)
    assert parsed.utcoffset() == timedelta(0)


async def test_parse_arrival_naive_timestamp_uses_the_default_time_zone(
    hass: HomeAssistant,
) -> None:
    await hass.config.async_set_time_zone("Europe/Paris")  # UTC+1 in January
    parsed = parse_arrival(State(ARRIVAL, "2026-01-10 12:00:00"), NOW)
    assert parsed == datetime(2026, 1, 10, 11, 0, tzinfo=UTC)


# ---------------------------------------------------------------- lifecycle


async def test_usable_follows_the_fit(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    assert not coordinator.data.usable
    coordinator.model.due = True
    await coordinator.async_refresh()
    assert coordinator.data.usable


async def test_home_assistant_stop_snapshots_and_schedules_the_save(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator._pending.append(coordinator.current_sample(NOW))
    coordinator.snapshot = None
    with patch.object(coordinator.storage, "async_schedule_save") as schedule:
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    assert coordinator._unsub_stop is None
    assert coordinator.snapshot == {
        "fake": True,
        "samples": len(coordinator.model.samples),
        "fitted": False,
    }
    assert NOW in [s.ts for s in coordinator.model.samples]
    schedule.assert_called_once_with(coordinator._store_data)


async def test_removed_input_state_is_not_sampled(hass: HomeAssistant, fake_model: None) -> None:
    """A state-changed event without a new state (entity removed) is ignored."""
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    before = len(coordinator.model.samples) + len(coordinator._pending)
    hass.states.async_remove(CLIMATE)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(coordinator.model.samples) + len(coordinator._pending) == before


async def test_nothing_is_sampled_once_unloading(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator._unloading = True
    event = MagicMock()
    event.data = {"new_state": State(CLIMATE, "heat"), "entity_id": CLIMATE}
    with patch.object(coordinator.config_entry, "async_create_background_task") as spawn:
        coordinator._async_handle_state_event(event)
    spawn.assert_not_called()
    pending = len(coordinator._pending)
    coordinator._async_grid_sample(NOW)
    assert len(coordinator._pending) == pending


async def test_unload_persists_the_model(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    with patch.object(coordinator.storage, "async_save", new=AsyncMock()) as save:
        await coordinator.async_unload()
    assert coordinator._unloading
    assert save.await_args.args[0]["model"] == coordinator.snapshot
    assert not coordinator._unsubs
    assert not coordinator._unsub_timers


async def test_snapshot_is_skipped_when_nothing_changed(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    await coordinator._async_snapshot(force=True)
    with patch.object(coordinator.model, "to_dict") as to_dict:
        await coordinator._async_snapshot()  # clean
        coordinator._dirty = True
        await coordinator._async_snapshot()  # dirty but taken moments ago
    to_dict.assert_not_called()
    coordinator._dirty = True
    await coordinator._async_snapshot(force=True)
    assert coordinator._dirty is False


# ---------------------------------------------------------------- update errors


async def test_failing_forecast_does_not_stop_the_zone(
    hass: HomeAssistant, fake_model: None, caplog: pytest.LogCaptureFixture
) -> None:
    set_inputs(hass, full=True)
    entry = await setup_entry(hass, make_entry(hass, full=True))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    with patch.object(
        coordinator.forecast, "async_get", AsyncMock(side_effect=RuntimeError("weather down"))
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.usable
    assert not coordinator.data.forecast_used
    assert f"Forecast of {WEATHER} failed" in caplog.text


async def test_failing_fit_is_logged_and_retried_later(
    hass: HomeAssistant, fake_model: None, caplog: pytest.LogCaptureFixture
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    with patch.object(coordinator.model, "fit", side_effect=ValueError("singular")):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.last_fit is None
    assert not coordinator.data.usable
    assert "Fit of Living Room failed" in caplog.text
    # the model still wants a fit: the next poll tries again and succeeds
    await coordinator.async_refresh()
    assert coordinator.last_fit is not None
    assert coordinator.data.usable


async def test_model_error_while_estimating_fails_the_update(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    with patch.object(coordinator.model, "preheat", side_effect=ArithmeticError("overflow")):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.translation_placeholders == {
        "zone": "Living Room",
        "error": "overflow",
    }


async def test_estimate_without_minutes_is_still_learning(
    hass: HomeAssistant, fake_model: None
) -> None:
    """A reachable estimate that carries no minutes cannot be reported as ok."""
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    estimate = PreheatEstimate(
        minutes=None,
        reachable=True,
        target_c=20.5,
        start_c=19.0,
        outdoor_used_c=None,
        max_reachable_c=24.0,
        reason={},
    )
    with patch.object(coordinator.model, "preheat", return_value=estimate):
        await coordinator.async_refresh()
    assert coordinator.data.status == "learning"


# ---------------------------------------------------------------- retrain


async def test_failing_history_load_still_bootstraps(
    hass: HomeAssistant, fake_model: None, no_history: Any, caplog: pytest.LogCaptureFixture
) -> None:
    no_history.return_value = [Sample(NOW - timedelta(days=1), 19.0, 5.0, 1.0)]
    set_inputs(hass)
    entry = make_entry(hass)
    with patch.object(FakeModel, "load_history", side_effect=TypeError("bad sample")):
        await setup_entry(hass, entry)
    coordinator: RTMCoordinator = entry.runtime_data
    assert "Loading history of Living Room failed" in caplog.text
    assert coordinator.bootstrapped
    assert not coordinator.bootstrapping
    assert coordinator.model.history == []
    assert coordinator.model.fits == 1  # forced because history was returned


async def test_retrain_while_unloading_does_not_refresh(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator._unloading = True
    with patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh:
        assert await coordinator.async_retrain() == 0
    refresh.assert_not_called()


# ---------------------------------------------------------------- lead time


def _estimate(minutes: float | None, *, reachable: bool, max_c: float | None, **reason: Any):
    return PreheatEstimate(
        minutes=minutes,
        reachable=reachable,
        target_c=20.5,
        start_c=19.0,
        outdoor_used_c=None,
        max_reachable_c=max_c,
        reason=reason,
    )


async def _coordinator(hass: HomeAssistant) -> RTMCoordinator:
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    return coordinator


@pytest.mark.parametrize(
    "estimate",
    [
        _estimate(None, reachable=True, max_c=24.0),
        _estimate(None, reachable=False, max_c=None),
        _estimate(None, reachable=False, max_c=24.0, status="no_outdoor"),
    ],
)
async def test_lead_unknown_without_minutes_and_nothing_to_aim_for(
    hass: HomeAssistant, fake_model: None, estimate: PreheatEstimate
) -> None:
    coordinator = await _coordinator(hass)
    assert coordinator._lead_minutes(NOW, 19.0, 20.5, 5.0, [], estimate, 120.0) is None


async def test_lead_is_the_whole_wait_when_even_the_reduced_goal_is_unreachable(
    hass: HomeAssistant, fake_model: None
) -> None:
    coordinator = await _coordinator(hass)
    estimate = _estimate(None, reachable=False, max_c=19.5)
    with patch.object(
        coordinator.model, "preheat", return_value=_estimate(None, reachable=False, max_c=19.5)
    ):
        assert coordinator._lead_minutes(NOW, 19.0, 20.5, 5.0, [], estimate, 120.0) == 120.0


async def test_lead_stops_when_the_coast_prediction_is_unavailable(
    hass: HomeAssistant, fake_model: None
) -> None:
    coordinator = await _coordinator(hass)
    estimate = _estimate(30.0, reachable=True, max_c=24.0)
    with patch.object(coordinator.model, "coast", return_value=None):
        assert coordinator._lead_minutes(NOW, 19.0, 20.5, 5.0, [], estimate, 120.0) == 30.0


async def test_lead_stops_when_the_refined_estimate_is_unreachable(
    hass: HomeAssistant, fake_model: None
) -> None:
    coordinator = await _coordinator(hass)
    estimate = _estimate(30.0, reachable=True, max_c=24.0)
    refined = _estimate(None, reachable=False, max_c=19.0)
    with patch.object(coordinator.model, "preheat", return_value=refined):
        assert coordinator._lead_minutes(NOW, 19.0, 20.5, 5.0, [], estimate, 120.0) == 30.0


async def test_lead_is_zero_wait_when_the_arrival_is_sooner_than_the_preheat(
    hass: HomeAssistant, fake_model: None
) -> None:
    coordinator = await _coordinator(hass)
    estimate = _estimate(30.0, reachable=True, max_c=24.0)
    assert coordinator._lead_minutes(NOW, 19.0, 20.5, 5.0, [], estimate, 10.0) == 30.0


# ---------------------------------------------------------------- timers


async def test_timer_refreshes_listeners_and_is_rearmed_by_each_update(
    hass: HomeAssistant, fake_model: None
) -> None:
    set_inputs(hass)
    arrival = dt_util.utcnow() + timedelta(hours=3)
    hass.states.async_set(ARRIVAL, arrival.isoformat(), {"device_class": "timestamp"})
    entry = await setup_entry(hass, make_entry(hass, arrival=True))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await coordinator.async_refresh()
    assert len(coordinator._unsub_timers) == 2  # preheat start and arrival
    with patch.object(coordinator, "async_update_listeners") as update:
        coordinator._async_timer_fired(NOW)
    update.assert_called_once_with()
    await coordinator.async_refresh()
    assert len(coordinator._unsub_timers) == 2  # replaced, not accumulated

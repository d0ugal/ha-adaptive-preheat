"""Self-calibration through the coordinator, the sensors and the Store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.adaptive_preheat.coordinator import RTMCoordinator
from custom_components.adaptive_preheat.rtm_core import RunScorer
from custom_components.adaptive_preheat.rtm_core.scoring import RunResult

from .conftest import (
    ARRIVAL,
    CLIMATE,
    SLUG,
    climate_attrs,
    make_entry,
    set_inputs,
    setup_entry,
)

PREHEAT = f"sensor.{SLUG}_preheat_minutes"
START = f"sensor.{SLUG}_preheat_start"
RUN_MAE = f"sensor.{SLUG}_run_mae"


def _result(day: int, predicted: float = 20.0, actual: float = 30.0) -> RunResult:
    started = datetime(2026, 1, 1, 6, 0, tzinfo=UTC) + timedelta(days=day)
    return RunResult(
        started_at=started,
        finished_at=started + timedelta(minutes=actual),
        outcome="reached",
        start_c=18.0,
        target_c=20.0,
        peak_c=20.0,
        predicted_minutes=predicted,
        actual_minutes=actual,
        heating_fraction=1.0,
        error_minutes=predicted - actual,
    )


def _seed(coordinator: RTMCoordinator, runs: int, predicted: float = 20.0, actual: float = 30.0):
    coordinator.scorer = RunScorer()
    coordinator.scorer.results = [_result(i, predicted, actual) for i in range(runs)]


async def _refresh(hass: HomeAssistant, coordinator: RTMCoordinator) -> None:
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


def _room(hass: HomeAssistant, current: float = 19.0, action: str = "idle") -> None:
    hass.states.async_set(
        CLIMATE, "heat", climate_attrs(current=current, target=20.5, action=action)
    )


async def test_no_evidence_leaves_the_estimate_alone(hass: HomeAssistant, fake_model: None) -> None:
    _room(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await _refresh(hass, coordinator)
    state = hass.states.get(PREHEAT)
    assert float(state.state) == 30.0
    assert state.attributes["calibration_factor"] == 1.0
    assert state.attributes["calibration_runs"] == 0
    assert state.attributes["uncalibrated_minutes"] == 30.0


async def test_evidence_scales_the_published_minutes(hass: HomeAssistant, fake_model: None) -> None:
    _room(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    _seed(coordinator, 8)  # the model said 20, the room took 30
    await _refresh(hass, coordinator)
    state = hass.states.get(PREHEAT)
    factor = state.attributes["calibration_factor"]
    assert 1.2 < factor < 1.5
    assert state.attributes["calibration_runs"] == 8
    assert state.attributes["uncalibrated_minutes"] == 30.0
    assert float(state.state) == round(30.0 * coordinator.data.calibration_factor, 1)


async def test_option_off_ignores_the_evidence(hass: HomeAssistant, fake_model: None) -> None:
    _room(hass)
    entry = await setup_entry(hass, make_entry(hass, self_calibrate=False))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    _seed(coordinator, 8)
    await _refresh(hass, coordinator)
    state = hass.states.get(PREHEAT)
    assert float(state.state) == 30.0
    assert state.attributes["calibration_factor"] == 1.0
    assert state.attributes["calibration_runs"] == 0


async def test_scoring_judges_the_raw_prediction(
    hass: HomeAssistant, fake_model: None, freezer: FrozenDateTimeFactory
) -> None:
    """A run opened while calibrated still records and is judged on the raw figure."""
    _room(hass, action="heating")
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    _seed(coordinator, 8)
    await _refresh(hass, coordinator)
    run = coordinator.scorer.open_run
    assert run is not None
    assert run.predicted_minutes == 30.0
    assert run.published_minutes > 30.0
    assert hass.states.get(RUN_MAE).attributes["open_run"]["predicted_minutes"] == 30.0

    freezer.tick(timedelta(minutes=20))
    _room(hass, 20.5, action="heating")
    await _refresh(hass, coordinator)
    last = coordinator.scorer.results[-1]
    assert last.error_minutes == 10.0  # raw 30 against 20, not the published figure
    assert last.published_error_minutes > 10.0


async def test_lead_time_uses_the_calibrated_figure(hass: HomeAssistant, fake_model: None) -> None:
    set_inputs(hass)
    _room(hass)
    arrival = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    hass.states.async_set(ARRIVAL, arrival, {"device_class": "timestamp"})
    entry = await setup_entry(hass, make_entry(hass, arrival=True))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    await _refresh(hass, coordinator)
    plain = coordinator.data.arrival - coordinator.data.preheat_start
    _seed(coordinator, 8)
    await _refresh(hass, coordinator)
    data = coordinator.data
    factor = data.calibration_factor
    lead = (data.arrival - data.preheat_start).total_seconds() / 60.0
    total = (data.arrival - data.updated_at).total_seconds() / 60.0
    assert lead > plain.total_seconds() / 60.0
    # The fake room cools 1 K/h and heats 3 K/h: the model lead L satisfies
    # L = 30 + (total - factor * L) / 3 and the published lead is factor * L.
    model_lead = lead / factor
    assert abs(model_lead - (30.0 + (total - lead) / 3.0)) < 0.5


async def test_factor_survives_a_reload_and_reset_clears_it(
    hass: HomeAssistant, fake_model: None
) -> None:
    _room(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    coordinator.model.due = True
    _seed(coordinator, 8)
    await _refresh(hass, coordinator)
    factor = hass.states.get(PREHEAT).attributes["calibration_factor"]
    assert factor > 1.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    coordinator.model.due = True
    await _refresh(hass, coordinator)
    assert hass.states.get(PREHEAT).attributes["calibration_factor"] == factor

    await coordinator.async_reset()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.calibration_factor == 1.0
    assert coordinator.data.calibration_runs == 0

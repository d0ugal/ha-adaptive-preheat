"""Recorder bootstrap: reducer behaviour, chunking and error handling."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from sqlalchemy.exc import SQLAlchemyError

from custom_components.adaptive_preheat import history
from custom_components.adaptive_preheat.const import HISTORY_CHUNK_DAYS
from custom_components.adaptive_preheat.history import (
    HistoryReducer,
    async_load_zone_history,
    reduce_states,
)
from custom_components.adaptive_preheat.inputs import ZoneInputs

from .conftest import CLIMATE, OUTDOOR, climate_attrs

C = "°C"
T0 = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)
INPUTS = ZoneInputs(climate=CLIMATE, outdoor_sensor=OUTDOOR)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def climate(minutes: float, current: float = 18.0, action: str = "idle", state: str = "heat"):
    ts = at(minutes)
    attrs = climate_attrs(current=current, action=action)
    return State(CLIMATE, state, attrs, last_updated=ts, last_changed=ts)


def outdoor(minutes: float, value: str) -> State:
    ts = at(minutes)
    return State(OUTDOOR, value, {"unit_of_measurement": C}, last_updated=ts, last_changed=ts)


def test_single_row_is_carried_forward_on_the_grid() -> None:
    """One recorder row holds until the end: a sample every 5 min, never at ``end``."""
    samples = reduce_states(INPUTS, C, [{CLIMATE: [climate(0)]}], at(22))
    assert [s.ts for s in samples] == [at(0), at(5), at(10), at(15), at(20)]
    assert {(s.indoor_c, s.effort, s.excluded) for s in samples} == {(18.0, 0.0, False)}


def test_end_exactly_on_grid_point_is_excluded() -> None:
    samples = reduce_states(INPUTS, C, [{CLIMATE: [climate(0)]}], at(10))
    assert [s.ts for s in samples] == [at(0), at(5)]


def test_no_rows_gives_no_samples() -> None:
    assert reduce_states(INPUTS, C, [{}, {CLIMATE: []}], at(60)) == []


def test_values_change_at_the_row_and_hold_after_it() -> None:
    rows = {CLIMATE: [climate(0, 18.0, "heating"), climate(12, 19.0, "idle")]}
    samples = {s.ts: s for s in reduce_states(INPUTS, C, [rows], at(30))}
    assert (samples[at(10)].indoor_c, samples[at(10)].effort) == (18.0, 1.0)
    assert (samples[at(12)].indoor_c, samples[at(12)].effort) == (19.0, 0.0)
    # the grid restarts from the last emitted sample, not from the previous grid point
    assert (samples[at(17)].indoor_c, samples[at(17)].effort) == (19.0, 0.0)
    assert at(15) not in samples


def test_burst_is_coalesced_keeping_first_timestamp_and_last_values() -> None:
    """Changes closer than the spacing (60 s) collapse into one sample."""
    rows = {
        CLIMATE: [climate(0, 18.0, "heating")],
        OUTDOOR: [outdoor(0.2, "3.0"), outdoor(0.5, "4.0")],
    }
    samples = reduce_states(INPUTS, C, [rows], at(4))
    assert len(samples) == 1
    assert samples[0].ts == at(0)
    assert samples[0].outdoor_c == 4.0
    assert samples[0].effort == 1.0


def test_changes_just_beyond_the_spacing_are_kept() -> None:
    rows = {CLIMATE: [climate(0, 18.0)], OUTDOOR: [outdoor(1.0, "3.0")]}
    samples = reduce_states(INPUTS, C, [rows], at(4))
    assert [(s.ts, s.outdoor_c) for s in samples] == [(at(0), None), (at(1), 3.0)]


def test_simultaneous_rows_of_several_entities_make_one_sample() -> None:
    rows = {CLIMATE: [climate(0, 18.0)], OUTDOOR: [outdoor(0, "3.0")]}
    samples = reduce_states(INPUTS, C, [rows], at(4))
    assert len(samples) == 1
    assert (samples[0].indoor_c, samples[0].outdoor_c) == (18.0, 3.0)


def test_rows_of_unknown_entities_are_ignored() -> None:
    rows = {CLIMATE: [climate(0)], "sensor.other": [State("sensor.other", "1")]}
    assert len(reduce_states(INPUTS, C, [rows], at(4))) == 1


def test_no_grid_fill_before_the_climate_has_a_state() -> None:
    """Only an outdoor row so far: one (excluded) sample, no carried grid."""
    samples = reduce_states(INPUTS, C, [{OUTDOOR: [outdoor(0, "3.0")]}], at(30))
    assert len(samples) == 1
    assert samples[0].excluded
    assert samples[0].outdoor_c == 3.0


def test_unavailable_climate_marks_gap_samples_excluded() -> None:
    rows = {CLIMATE: [climate(0), climate(10, state="unavailable"), climate(20, 19.0)]}
    samples = {s.ts: s for s in reduce_states(INPUTS, C, [rows], at(30))}
    assert not samples[at(5)].excluded
    assert samples[at(10)].excluded
    assert samples[at(15)].excluded
    assert samples[at(20)].indoor_c == 19.0
    assert not samples[at(20)].excluded


def test_reducer_state_carries_across_windows_without_gaps() -> None:
    reducer = HistoryReducer(INPUTS, C)
    reducer.feed({CLIMATE: [climate(0, 18.0)]})
    reducer.feed({})  # an empty window changes nothing
    reducer.feed({OUTDOOR: [outdoor(23, "2.0")]})
    samples = reducer.finish(at(40))
    times = [s.ts for s in samples]
    assert times == sorted(times)
    assert max(b - a for a, b in itertools.pairwise(times)) <= timedelta(minutes=5)
    assert samples[-1].indoor_c == 18.0
    assert samples[-1].outdoor_c == 2.0
    assert samples is reducer.samples


def test_rows_within_a_window_are_merged_chronologically() -> None:
    """Entities are interleaved by timestamp, not processed entity by entity."""
    rows = {
        OUTDOOR: [outdoor(10, "5.0")],
        CLIMATE: [climate(0, 18.0), climate(20, 19.0)],
    }
    samples = {s.ts: s for s in reduce_states(INPUTS, C, [rows], at(25))}
    assert samples[at(10)].outdoor_c == 5.0
    assert samples[at(10)].indoor_c == 18.0
    assert samples[at(20)].indoor_c == 19.0
    assert samples[at(20)].outdoor_c == 5.0


# ---------------------------------------------------------------- recorder access


def test_fetch_states_queries_the_recorder_and_drops_non_states(hass: HomeAssistant) -> None:
    row = climate(0)
    result: dict[str, Any] = {CLIMATE: [row, {"state": "heat"}], OUTDOOR: []}
    with patch(
        "homeassistant.components.recorder.history.get_significant_states", return_value=result
    ) as query:
        out = history._fetch_states(hass, [CLIMATE, OUTDOOR], at(0), at(60))
    assert out == {CLIMATE: [row], OUTDOOR: []}
    args, kwargs = query.call_args
    assert args == (hass, at(0), at(60))
    assert kwargs["entity_ids"] == [CLIMATE, OUTDOOR]
    assert kwargs["include_start_time_state"] is True
    assert kwargs["significant_changes_only"] is False
    assert kwargs["minimal_response"] is False
    assert kwargs["no_attributes"] is False


def test_load_chunked_queries_contiguous_windows_up_to_end(hass: HomeAssistant) -> None:
    start = T0
    end = T0 + timedelta(days=2 * HISTORY_CHUNK_DAYS + 3)
    calls: list[tuple[datetime, datetime]] = []

    def fetch(_hass, entity_ids, chunk_start, chunk_end):
        assert entity_ids == INPUTS.sample_entities
        calls.append((chunk_start, chunk_end))
        # the recorder reports the state at the window start (include_start_time_state)
        state = State(
            CLIMATE,
            "heat",
            climate_attrs(current=18.0),
            last_updated=chunk_start,
            last_changed=chunk_start,
        )
        return {CLIMATE: [state]}

    with patch.object(history, "_fetch_states", side_effect=fetch):
        samples = history._load_chunked(hass, INPUTS, C, start, end)

    week = timedelta(days=HISTORY_CHUNK_DAYS)
    assert calls == [
        (start, start + week),
        (start + week, start + 2 * week),
        (start + 2 * week, end),
    ]
    times = [s.ts for s in samples]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    assert times[0] == start
    assert end - times[-1] <= timedelta(minutes=5)
    assert max(b - a for a, b in itertools.pairwise(times)) <= timedelta(minutes=5)


def test_load_chunked_short_range_is_a_single_query(hass: HomeAssistant) -> None:
    with patch.object(history, "_fetch_states", return_value={}) as fetch:
        assert history._load_chunked(hass, INPUTS, C, at(0), at(90)) == []
    assert fetch.call_count == 1
    assert fetch.call_args.args[2:] == (at(0), at(90))


def test_load_chunked_empty_range_does_not_query(hass: HomeAssistant) -> None:
    with patch.object(history, "_fetch_states") as fetch:
        assert history._load_chunked(hass, INPUTS, C, at(0), at(0)) == []
    fetch.assert_not_called()


async def test_async_load_without_recorder_returns_empty(hass: HomeAssistant) -> None:
    assert "recorder" not in hass.config.components
    assert await async_load_zone_history(hass, INPUTS, 30) == []


def _recorder(job: AsyncMock) -> MagicMock:
    instance = MagicMock()
    instance.async_add_executor_job = job
    return instance


async def test_async_load_runs_in_the_recorder_executor(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    job = AsyncMock(return_value=["sample"])
    with patch("homeassistant.components.recorder.get_instance", return_value=_recorder(job)):
        assert await async_load_zone_history(hass, INPUTS, 10) == ["sample"]
    func, *args = job.call_args.args
    assert func is history._load_chunked
    hass_arg, inputs, unit, start, end = args
    assert (hass_arg, inputs, unit) == (hass, INPUTS, hass.config.units.temperature_unit)
    assert end - start == timedelta(days=10)


@pytest.mark.parametrize(
    "error",
    [SQLAlchemyError("db locked"), HomeAssistantError("x"), KeyError("k"), ValueError("v")],
)
async def test_async_load_recorder_errors_give_no_history(
    hass: HomeAssistant, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    hass.config.components.add("recorder")
    job = AsyncMock(side_effect=error)
    with patch("homeassistant.components.recorder.get_instance", return_value=_recorder(job)):
        assert await async_load_zone_history(hass, INPUTS, 30) == []
    assert f"Could not read history for {CLIMATE}" in caplog.text


async def test_async_load_does_not_swallow_programming_errors(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    job = AsyncMock(side_effect=RuntimeError("bug"))
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=_recorder(job)),
        pytest.raises(RuntimeError),
    ):
        await async_load_zone_history(hass, INPUTS, 30)

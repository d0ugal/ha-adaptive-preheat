"""The import_history service: file parsing, model work, persistence across a restart."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_thermal_model.const import DOMAIN
from custom_components.room_thermal_model.coordinator import RTMCoordinator
from custom_components.room_thermal_model.rtm_core.base import RECOMMENDED_STEP_MINUTES, Sample
from custom_components.room_thermal_model.rtm_core.synthetic import generate, radiator_room

from .conftest import make_entry, set_inputs, setup_entry

NOW = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)


def write_history(path: Path, samples: list[Sample]) -> Path:
    lines = [
        json.dumps(
            {
                "ts": s.ts.isoformat(),
                "indoor_c": s.indoor_c,
                "outdoor_c": s.outdoor_c,
                "effort": s.effort,
            }
        )
        for s in samples
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def rows(days: float, end: datetime = NOW) -> list[Sample]:
    start = end - timedelta(days=days)
    return generate(radiator_room(), days, start=start, sample_minutes=5).samples


@pytest.fixture
def allowed(hass: HomeAssistant, tmp_path: Path) -> Path:
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    return tmp_path


async def call(hass: HomeAssistant, path: Path | str, **extra: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN,
        "import_history",
        {"path": str(path), **extra},
        blocking=True,
        return_response=True,
    )


async def test_import_merges_refits_and_reports(
    hass: HomeAssistant, fake_model: None, allowed: Path, freezer: Any
) -> None:
    freezer.move_to(NOW)
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    samples = rows(2)
    # older than history_days (30) before the newest row: dropped by the model, reported
    old = [Sample(NOW - timedelta(days=45), 18.0, 5.0, 0.0)]
    path = write_history(allowed / "h.jsonl", [*samples[::-1], *old])
    fits = coordinator.model.fits

    result = await call(hass, path)

    assert result["samples"] == len(samples) + 1
    assert result["dropped_older_than_history_days"] == 1
    assert result["reordered"]
    assert result["duplicates"] == 0
    assert result["first"] == old[0].ts.isoformat()
    assert len(coordinator.model.history) == len(samples) + 1  # merge: nothing replaced
    assert coordinator.model.fits == fits + 1
    assert coordinator.bootstrapped
    assert coordinator.snapshot is not None

    await call(hass, path, replace=True)
    assert len(coordinator.model.history) == len(samples) + 1  # FakeModel cleared then loaded


async def test_import_rejections(
    hass: HomeAssistant, fake_model: None, allowed: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    set_inputs(hass)
    await setup_entry(hass, make_entry(hass))
    with pytest.raises(ServiceValidationError, match="not in an allowed folder"):
        await call(hass, tmp_path_factory.mktemp("elsewhere") / "h.jsonl")
    with pytest.raises(ServiceValidationError, match="cannot read"):
        await call(hass, allowed / "missing.jsonl")
    bad = allowed / "bad.jsonl"
    bad.write_text('{"ts": "2026-01-10T08:00:00"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ServiceValidationError, match=r"line 1: ts .* no UTC offset"):
        await call(hass, bad)
    binary = allowed / "binary.jsonl"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ServiceValidationError, match="cannot read"):
        await call(hass, binary)


async def test_import_needs_exactly_one_zone(
    hass: HomeAssistant, fake_model: None, allowed: Path
) -> None:
    set_inputs(hass)
    await setup_entry(hass, make_entry(hass))
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second",
        unique_id="climate.second",
        data={"zone_name": "Second", "climate_entity": "climate.second"},
    )
    second.add_to_hass(hass)
    hass.states.async_set("climate.second", "heat", {"current_temperature": 19.0})
    await setup_entry(hass, second)
    path = write_history(allowed / "h.jsonl", rows(1))
    with pytest.raises(ServiceValidationError, match="exactly one zone"):
        await call(hass, path)
    result = await call(hass, path, config_entry_id=second.entry_id)
    assert result["samples"] > 0


async def test_imported_history_survives_a_restart(
    hass: HomeAssistant, allowed: Path, freezer: Any, hass_storage: dict[str, Any]
) -> None:
    """Real model: the import trains it, and the stored snapshot restores the rows."""
    freezer.move_to(NOW)
    set_inputs(hass)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator: RTMCoordinator = entry.runtime_data
    path = write_history(allowed / "h.jsonl", rows(20))

    result = await call(hass, path)

    assert result["dropped_older_than_history_days"] == 0
    quality = coordinator.model.quality()
    assert quality.samples > 0.9 * 20 * 24 * 60 / RECOMMENDED_STEP_MINUTES
    assert coordinator.model.parameters() is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert stored["bootstrapped"]

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    restored: RTMCoordinator = entry.runtime_data
    assert restored.model is not coordinator.model
    assert restored.model.quality().samples >= quality.samples
    assert restored.model.parameters() is not None
    assert restored.bootstrapped  # no second bootstrap that could replace the import

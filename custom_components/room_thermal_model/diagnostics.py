"""Diagnostics support."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import RTMConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RTMConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for one zone (nothing sensitive: no location data)."""
    coordinator = entry.runtime_data
    data = coordinator.data
    forecast = coordinator.forecast.cache if coordinator.forecast else None
    zone: dict[str, Any] | None = None
    if data is not None:
        zone = {
            "updated_at": data.updated_at,
            "indoor_c": data.indoor_c,
            "outdoor_c": data.outdoor_c,
            "effort": data.effort,
            "target_c": data.target_c,
            "target_source": data.target_source,
            "status": data.status,
            "preheat": asdict(data.preheat) if data.preheat else None,
            "parameters": asdict(data.parameters) if data.parameters else None,
            "quality": asdict(data.quality),
            "forecast_used": data.forecast_used,
            "arrival": data.arrival,
            "preheat_start": data.preheat_start,
            "coast_c": data.coast_c,
        }
    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "inputs": {
            entity_id: (
                {"state": state.state, "attributes": dict(state.attributes)}
                if (state := hass.states.get(entity_id)) is not None
                else None
            )
            for entity_id in coordinator.inputs.all_entities
        },
        "model_config": asdict(coordinator.model_config),
        "bootstrapped": coordinator.bootstrapped,
        "bootstrapping": coordinator.bootstrapping,
        "last_fit": coordinator.last_fit,
        "last_fit_summary": coordinator.last_fit_summary,
        "forecast": (
            {
                "type": forecast.forecast_type,
                "fetched_at": forecast.fetched_at,
                "points": len(forecast.points),
                "first": asdict(forecast.points[0]) if forecast.points else None,
                "error": forecast.error,
            }
            if forecast
            else None
        ),
        "scoring": coordinator.scorer.to_dict(),
        "zone": zone,
        "model": coordinator.snapshot,
    }

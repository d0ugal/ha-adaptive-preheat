"""Weather forecast fetch, fallback order and cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from custom_components.adaptive_preheat.forecast import WeatherForecast, parse_forecast

from .conftest import WEATHER

NOW = datetime(2026, 1, 10, 6, 0, tzinfo=UTC)


def _items(kind: str) -> list[dict[str, Any]]:
    step = {"hourly": 1, "twice_daily": 12, "daily": 24}[kind]
    return [
        {
            "datetime": (NOW + timedelta(hours=i * step)).isoformat(),
            "temperature": 10.0 + i,
            "templow": 2.0 + i,
        }
        for i in range(3)
    ]


def register_weather(
    hass: HomeAssistant, failing: set[str] | None = None, empty: set[str] | None = None
) -> list[str]:
    """Register a fake ``weather.get_forecasts`` service; returns the types requested."""
    calls: list[str] = []
    failing = failing or set()
    empty = empty or set()

    async def _handler(call: ServiceCall) -> dict[str, Any]:
        kind = call.data["type"]
        calls.append(kind)
        if kind in failing:
            raise HomeAssistantError(f"{kind} not supported")
        entity_ids = call.data["entity_id"]
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        return {e: {"forecast": [] if kind in empty else _items(kind)} for e in entity_ids}

    hass.services.async_register(
        "weather", "get_forecasts", _handler, supports_response=SupportsResponse.ONLY
    )
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 6.0, "temperature_unit": "°C"})
    return calls


async def test_hourly_first(hass: HomeAssistant) -> None:
    """Hourly forecast is used as is (no templow averaging)."""
    calls = register_weather(hass)
    forecast = WeatherForecast(hass, WEATHER)
    points = await forecast.async_get(NOW)
    assert calls == ["hourly"]
    assert [p.outdoor_c for p in points] == [10.0, 11.0, 12.0]
    assert forecast.cache.forecast_type == "hourly"
    assert points[0].ts == NOW


async def test_fallback_order(hass: HomeAssistant) -> None:
    """hourly fails, twice_daily empty → daily (mean of high and low)."""
    calls = register_weather(hass, failing={"hourly"}, empty={"twice_daily"})
    forecast = WeatherForecast(hass, WEATHER)
    points = await forecast.async_get(NOW)
    assert calls == ["hourly", "twice_daily", "daily"]
    assert forecast.cache.forecast_type == "daily"
    assert [p.outdoor_c for p in points] == [6.0, 7.0, 8.0]


async def test_all_fail_cached(hass: HomeAssistant) -> None:
    """No forecast at all: empty list, error kept, no retry within the cache period."""
    calls = register_weather(hass, failing={"hourly", "twice_daily", "daily"})
    forecast = WeatherForecast(hass, WEATHER)
    assert await forecast.async_get(NOW) == []
    assert forecast.cache.error
    assert await forecast.async_get(NOW + timedelta(minutes=10)) == []
    assert len(calls) == 3


async def test_cache_30_minutes(hass: HomeAssistant) -> None:
    """The forecast is fetched again only after 30 minutes."""
    calls = register_weather(hass)
    forecast = WeatherForecast(hass, WEATHER)
    await forecast.async_get(NOW)
    await forecast.async_get(NOW + timedelta(minutes=29))
    assert calls == ["hourly"]
    await forecast.async_get(NOW + timedelta(minutes=31))
    assert calls == ["hourly", "hourly"]


async def test_no_weather_service(hass: HomeAssistant) -> None:
    """Weather integration missing: no exception, empty forecast."""
    forecast = WeatherForecast(hass, WEATHER)
    assert await forecast.async_get(NOW) == []


@pytest.mark.parametrize(
    ("unit", "expected"), [("°C", 10.0), ("°F", pytest.approx(-12.222, abs=1e-3))]
)
def test_parse_units_and_garbage(unit: str, expected: float) -> None:
    """Units are converted; malformed items skipped; points sorted."""
    items = [
        {"datetime": (NOW + timedelta(hours=1)).isoformat(), "temperature": 11.0},
        {"datetime": NOW.isoformat(), "temperature": 10.0},
        {"datetime": "nonsense", "temperature": 3.0},
        {"datetime": NOW.isoformat(), "temperature": None},
        "garbage",
    ]
    points = parse_forecast(items, "hourly", unit)
    assert len(points) == 2
    assert points[0].ts == NOW
    assert points[0].outdoor_c == expected

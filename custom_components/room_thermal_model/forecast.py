"""Outdoor temperature forecast from a ``weather`` entity (``weather.get_forecasts``).

Forecast types are tried in order ``hourly`` → ``twice_daily`` → ``daily``; the first
one that returns at least one usable point wins. For ``twice_daily`` / ``daily`` points
the mean of ``temperature`` and ``templow`` is used when both are present (a daily high
alone overstates the outdoor temperature of a preheat window). The result is cached for
``FORECAST_CACHE_MINUTES``; a failed fetch is cached too (no retry storm).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import FORECAST_CACHE_MINUTES, FORECAST_TYPES
from .inputs import ATTR_WEATHER_TEMPERATURE_UNIT, to_celsius, to_float
from .rtm_core.base import ForecastPoint

_LOGGER = logging.getLogger(__name__)

WEATHER_DOMAIN = "weather"
SERVICE_GET_FORECASTS = "get_forecasts"
ATTR_FORECAST = "forecast"
ATTR_DATETIME = "datetime"
ATTR_TEMPLOW = "templow"


@dataclass
class ForecastCache:
    """Last fetched forecast."""

    points: list[ForecastPoint] = field(default_factory=list)
    forecast_type: str | None = None
    fetched_at: datetime | None = None
    error: str | None = None


def parse_forecast(items: Any, forecast_type: str, unit: str | None) -> list[ForecastPoint]:
    """Convert ``weather.get_forecasts`` items into sorted forecast points (°C)."""
    points: list[ForecastPoint] = []
    if not isinstance(items, list):
        return points
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = item.get(ATTR_DATETIME)
        when = ts if isinstance(ts, datetime) else dt_util.parse_datetime(str(ts or ""))
        high = to_float(item.get(ATTR_TEMPERATURE))
        if when is None or high is None:
            continue
        value = high
        low = to_float(item.get(ATTR_TEMPLOW))
        if forecast_type != "hourly" and low is not None:
            value = (high + low) / 2.0
        celsius = to_celsius(value, unit)
        if celsius is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.get_default_time_zone())
        points.append(ForecastPoint(ts=dt_util.as_utc(when), outdoor_c=celsius))
    points.sort(key=lambda p: p.ts)
    return points


class WeatherForecast:
    """Fetch and cache the forecast of one weather entity."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.cache = ForecastCache()

    def _unit(self) -> str:
        state = self.hass.states.get(self.entity_id)
        unit = state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT) if state else None
        return str(unit or self.hass.config.units.temperature_unit)

    async def _async_fetch_type(self, forecast_type: str) -> list[ForecastPoint]:
        response = await self.hass.services.async_call(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
            {"type": forecast_type},
            target={"entity_id": self.entity_id},
            blocking=True,
            return_response=True,
        )
        entity = (response or {}).get(self.entity_id) or {}
        items = entity.get(ATTR_FORECAST) if isinstance(entity, dict) else None
        return parse_forecast(items, forecast_type, self._unit())

    async def async_get(self, now: datetime, force: bool = False) -> list[ForecastPoint]:
        """Return the cached forecast, refreshing it when older than the cache period."""
        cache = self.cache
        if (
            not force
            and cache.fetched_at is not None
            and now - cache.fetched_at < timedelta(minutes=FORECAST_CACHE_MINUTES)
        ):
            return cache.points
        errors: list[str] = []
        points: list[ForecastPoint] = []
        used: str | None = None
        for forecast_type in FORECAST_TYPES:
            try:
                points = await self._async_fetch_type(forecast_type)
            except (HomeAssistantError, vol.Invalid) as err:
                errors.append(f"{forecast_type}: {err}")
                continue
            if points:
                used = forecast_type
                break
            errors.append(f"{forecast_type}: empty")
        if used is None:
            _LOGGER.debug("No forecast from %s (%s)", self.entity_id, "; ".join(errors))
        self.cache = ForecastCache(
            points=points if used else [],
            forecast_type=used,
            fetched_at=now,
            error=None if used else "; ".join(errors)[:255],
        )
        return self.cache.points

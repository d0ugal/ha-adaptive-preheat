"""Sensor platform."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    KEY_DEAD_TIME,
    KEY_HEATING_RATE,
    KEY_LAST_RUN_ERROR,
    KEY_MAX_REACHABLE,
    KEY_MODEL_MATURITY,
    KEY_PREHEAT_MINUTES,
    KEY_PREHEAT_START,
    KEY_RUN_MAE,
    KEY_TIME_CONSTANT,
)
from .coordinator import RTMConfigEntry, RTMCoordinator
from .entity import RTMEntity
from .rtm_core import RunResult

PARALLEL_UPDATES = 0

UNIT_KELVIN_PER_HOUR = "K/h"


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 (HA platform signature)
    entry: RTMConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        PreheatMinutesSensor(coordinator),
        MaxReachableSensor(coordinator),
        ModelMaturitySensor(coordinator),
        TimeConstantSensor(coordinator),
        HeatingRateSensor(coordinator),
        DeadTimeSensor(coordinator),
        LastRunErrorSensor(coordinator),
        RunMaeSensor(coordinator),
    ]
    if coordinator.inputs.has_arrival:
        entities.append(PreheatStartSensor(coordinator))
    async_add_entities(entities)


class RTMSensor(RTMEntity, SensorEntity):
    """Base zone sensor."""

    _key_name: str

    def __init__(self, coordinator: RTMCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, self._key_name, ENTITY_ID_FORMAT)


class PreheatMinutesSensor(RTMSensor):
    """Minutes of full heating needed to reach the target (unknown while learning)."""

    _key_name = KEY_PREHEAT_MINUTES
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def _schedule(self) -> bool:
        return self.coordinator.schedule is not None

    @property
    def native_value(self) -> float | None:
        """Minutes (None when learning, unreachable or no temperature)."""
        zone = self.zone
        if zone is None or zone.preheat_minutes is None:
            return None
        return round(max(0.0, zone.preheat_minutes), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Inputs and explanation of the estimate."""
        zone = self.zone
        if zone is None:
            return {}
        estimate = zone.preheat
        return {
            "target_c": _round(zone.target_c),
            "target_source": zone.target_source,
            "reachable": estimate.reachable if estimate else None,
            "start_c": _round(estimate.start_c if estimate else zone.indoor_c),
            "outdoor_used_c": _round(estimate.outdoor_used_c if estimate else zone.outdoor_c),
            "max_reachable_c": _round(estimate.max_reachable_c if estimate else None),
            "forecast_used": zone.forecast_used,
            "maturity": round(zone.quality.maturity * 100, 1),
            "reason": zone.status,
            "trusted": zone.trusted,
            "trust_reasons": list(zone.trust_reasons),
            "details": dict(estimate.reason) if estimate else {},
            "arrival": zone.arrival.isoformat() if zone.arrival else None,
            "coast_c": _round(zone.coast_c),
            **({"next_block_target_c": _round(zone.next_block_target_c)} if self._schedule else {}),
            "calibration_factor": _round(zone.calibration_factor, 3),
            "calibration_runs": zone.calibration_runs,
            "uncalibrated_minutes": _round(zone.uncalibrated_minutes, 1),
        }


class PreheatStartSensor(RTMSensor):
    """When heating must start to reach the target at the arrival."""

    _key_name = KEY_PREHEAT_START
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Arrival minus preheat minutes (unknown without a future arrival or estimate)."""
        zone = self.zone
        return zone.preheat_start if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Arrival used, where it came from and the next schedule block."""
        zone = self.zone
        if zone is None:
            return {}
        return {
            "arrival": zone.arrival.isoformat() if zone.arrival else None,
            "arrival_source": zone.arrival_source,
            "arrival_target_c": _round(zone.arrival_target_c),
            "next_block_start": zone.next_block_start.isoformat()
            if zone.next_block_start
            else None,
            "next_block_target_c": _round(zone.next_block_target_c),
            "reason": zone.status,
        }


class MaxReachableSensor(RTMSensor):
    """Equilibrium temperature at full heating effort."""

    _key_name = KEY_MAX_REACHABLE
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """T_infinity (unknown before the first fit)."""
        zone = self.zone
        if zone is None or zone.preheat is None:
            return None
        return _round(zone.preheat.max_reachable_c)


class ModelMaturitySensor(RTMSensor):
    """How well the model knows the zone."""

    _key_name = KEY_MODEL_MATURITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Maturity in percent."""
        zone = self.zone
        return round(zone.quality.maturity * 100, 1) if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Quality report."""
        zone = self.zone
        if zone is None:
            return {}
        quality = zone.quality
        return {
            "samples": quality.samples,
            "heating_hours": _round(quality.heating_hours, 1),
            "cooling_hours": _round(quality.cooling_hours, 1),
            "rmse_c": _round(quality.rmse_c, 3),
            "baseline_rmse_c": _round(quality.baseline_rmse_c, 3),
            "duration_mae_minutes": _round(quality.duration_mae_minutes, 1),
            "episodes": quality.episodes,
            "last_fit": zone.last_fit.isoformat() if zone.last_fit else None,
            "bootstrapping": self.coordinator.bootstrapping,
            "trusted": zone.trusted,
            "trust_reasons": list(zone.trust_reasons),
            "zone_group": self.coordinator.zone_group,
            "prior_source": self.coordinator.prior_info["source"],
            "prior_peers": list(self.coordinator.prior_info["peers"]),
        }


class RTMParameterSensor(RTMSensor):
    """Diagnostic sensor exposing one learnt parameter."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _value(self) -> float | None:
        raise NotImplementedError

    @property
    def native_value(self) -> float | None:
        """Unknown before the first fit."""
        zone = self.zone
        if zone is None or zone.parameters is None:
            return None
        value = self._value()
        return _round(value) if value is not None and value != float("inf") else None


class TimeConstantSensor(RTMParameterSensor):
    """Time constant tau = 1/b."""

    _key_name = KEY_TIME_CONSTANT
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1

    def _value(self) -> float | None:
        assert self.zone is not None
        assert self.zone.parameters is not None
        return self.zone.parameters.time_constant_hours


class HeatingRateSensor(RTMParameterSensor):
    """Heating gain a (K/h at full effort)."""

    _key_name = KEY_HEATING_RATE
    _attr_native_unit_of_measurement = UNIT_KELVIN_PER_HOUR
    _attr_suggested_display_precision = 2

    def _value(self) -> float | None:
        assert self.zone is not None
        assert self.zone.parameters is not None
        return self.zone.parameters.heating_rate_k_per_h


class DeadTimeSensor(RTMParameterSensor):
    """Dead time d (radiator and valve lag)."""

    _key_name = KEY_DEAD_TIME
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def _value(self) -> float | None:
        assert self.zone is not None
        assert self.zone.parameters is not None
        return self.zone.parameters.dead_time_minutes


def _run_attributes(run: RunResult) -> dict[str, Any]:
    return {
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
        "outcome": run.outcome,
        "start_c": _round(run.start_c),
        "target_c": _round(run.target_c),
        "peak_c": _round(run.peak_c),
        "predicted_minutes": _round(run.predicted_minutes, 1),
        "actual_minutes": _round(run.actual_minutes, 1),
        "error_minutes": _round(run.error_minutes, 1),
        "heating_fraction": _round(run.heating_fraction),
        "clean": run.clean,
    }


class RTMScoringSensor(RTMSensor):
    """Diagnostic sensor reporting how the live preheat predictions turned out."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Counts, the latest runs and the run in progress."""
        zone = self.zone
        scoring = zone.scoring if zone else None
        if scoring is None:
            return {}
        run = scoring.open_run
        return {
            "runs": scoring.runs,
            "reached": scoring.reached,
            "clean_runs": scoring.clean,
            "mae_minutes": _round(scoring.mae_minutes, 1),
            "bias_minutes": _round(scoring.bias_minutes, 1),
            "open_run": (
                {
                    "started_at": run.started_at.isoformat(),
                    "start_c": _round(run.start_c),
                    "target_c": _round(run.target_c),
                    "predicted_minutes": _round(run.predicted_minutes, 1),
                }
                if run
                else None
            ),
            "last_run": _run_attributes(scoring.last) if scoring.last else None,
        }


class LastRunErrorSensor(RTMScoringSensor):
    """Signed error of the newest scored run: predicted minus actual (positive = too slow)."""

    _key_name = KEY_LAST_RUN_ERROR

    @property
    def native_value(self) -> float | None:
        """Error of the newest run that reached its target (unknown before the first)."""
        zone = self.zone
        scoring = zone.scoring if zone else None
        if scoring is None:
            return None
        for run in reversed(self.coordinator.scorer.results):
            if run.error_minutes is not None:
                return _round(run.error_minutes, 1)
        return None


class RunMaeSensor(RTMScoringSensor):
    """Mean absolute error over the clean runs kept (heating for most of the run)."""

    _key_name = KEY_RUN_MAE

    @property
    def native_value(self) -> float | None:
        """Minutes (unknown before the first clean run)."""
        zone = self.zone
        scoring = zone.scoring if zone else None
        return _round(scoring.mae_minutes, 1) if scoring else None

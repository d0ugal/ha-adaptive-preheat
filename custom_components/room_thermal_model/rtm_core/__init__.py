"""Pure-Python thermal model core (stdlib only, no Home Assistant imports)."""

from __future__ import annotations

from .base import (
    ForecastPoint,
    ModelConfig,
    PreheatEstimate,
    QualityReport,
    Sample,
    ThermalModel,
    ThermalParameters,
)
from .model import RCThermalModel


def create_model(config: ModelConfig | None = None) -> ThermalModel:
    """Factory used by the integration (single backend for now)."""
    return RCThermalModel(config or ModelConfig())


__all__ = [
    "ForecastPoint",
    "ModelConfig",
    "PreheatEstimate",
    "QualityReport",
    "RCThermalModel",
    "Sample",
    "ThermalModel",
    "ThermalParameters",
    "create_model",
]

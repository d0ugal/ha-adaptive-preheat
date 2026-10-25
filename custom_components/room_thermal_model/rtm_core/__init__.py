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
from .calibration import Calibration, calibration_factor
from .model import RCThermalModel
from .scoring import RunResult, RunScorer, ScoringSummary
from .shared_prior import GroupPrior, PeerSummary, build_group_prior


def create_model(config: ModelConfig | None = None) -> ThermalModel:
    """Factory used by the integration (single backend for now)."""
    return RCThermalModel(config or ModelConfig())


__all__ = [
    "Calibration",
    "ForecastPoint",
    "GroupPrior",
    "ModelConfig",
    "PeerSummary",
    "PreheatEstimate",
    "QualityReport",
    "RCThermalModel",
    "RunResult",
    "RunScorer",
    "Sample",
    "ScoringSummary",
    "ThermalModel",
    "ThermalParameters",
    "build_group_prior",
    "calibration_factor",
    "create_model",
]

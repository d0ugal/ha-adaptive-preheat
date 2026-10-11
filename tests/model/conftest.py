"""Pure model tests: import ``rtm_core`` directly (never the Home Assistant package)."""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "room_thermal_model"
if str(INTEGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_DIR))

from rtm_core import ModelConfig, RCThermalModel  # noqa: E402
from rtm_core import synthetic as syn  # noqa: E402

ROOMS = syn.ROOMS


@cache
def synthetic_run(name: str, days: float, windows: bool = False) -> syn.SyntheticRun:
    return syn.generate(ROOMS[name](), days, windows=windows)


@cache
def _trained_state(name: str, days: float, windows: bool) -> dict:
    run = synthetic_run(name, days, windows)
    model = RCThermalModel(ModelConfig())
    model.load_history(run.samples, replace=True)
    model.fit(run.samples[-1].ts)
    return model.to_dict()


def trained_model(name: str, days: float, windows: bool = False) -> RCThermalModel:
    """Fresh model restored from a cached trained state (tests may not share instances)."""
    return RCThermalModel.from_dict(_trained_state(name, days, windows), ModelConfig())


@pytest.fixture
def trained():
    return trained_model


@pytest.fixture
def run_of():
    return synthetic_run

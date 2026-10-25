"""Long imported history: merging, trimming by ``history_days`` and the stored size."""

from __future__ import annotations

import json
from datetime import timedelta

from rtm_core import ModelConfig, RCThermalModel
from rtm_core import synthetic as syn


def run(days: float):
    return syn.generate(syn.ROOMS["radiator"](), days, sample_minutes=5)


def test_merging_recent_rows_keeps_the_older_imported_ones():
    samples = run(60).samples
    model = RCThermalModel(ModelConfig(history_days=90))
    model.load_history(samples, replace=True)
    before = model.quality().samples
    recent = [s for s in samples if s.ts > samples[-1].ts - timedelta(days=10)]
    model.load_history(recent)  # the recorder bootstrap merges like this
    assert model.quality().samples == before


def test_rows_older_than_history_days_are_trimmed_and_longer_keeps_them():
    samples = run(60).samples
    short = RCThermalModel(ModelConfig(history_days=30))
    short.load_history(samples, replace=True)
    long = RCThermalModel(ModelConfig(history_days=90))
    long.load_history(samples, replace=True)
    per_day = 288
    assert abs(short.quality().samples - 30 * per_day) < 2 * per_day
    assert long.quality().samples > 55 * per_day


def test_stored_snapshot_is_compact_and_restores_every_row():
    samples = run(60).samples
    config = ModelConfig(history_days=90)
    model = RCThermalModel(config)
    model.load_history(samples, replace=True)
    model.fit(samples[-1].ts)
    text = json.dumps(model.to_dict(), separators=(",", ":"))
    assert len(text) < 60 * 288 * 12  # well under 12 bytes per row (plain form: ~42)
    restored = RCThermalModel.from_dict(json.loads(text), config)
    assert restored.quality().samples == model.quality().samples
    assert restored.parameters() == model.parameters()

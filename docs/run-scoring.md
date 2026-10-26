# Run scoring, self-calibration and the back-test

## Run scoring

The model also marks its own homework. When the zone is heating, the room is at least 0.5 K
below the target and a preheat estimate exists, a run opens with that first prediction. It
closes when the room gets within 0.2 K of the target:

- `last_run_error` is the signed error of the newest scored run, `predicted - actual` in
  minutes (positive: the model expected a longer warm-up than it took);
- `run_mae` is the mean absolute error over the clean runs kept (the last 20 results), with
  `bias_minutes`, counts, the run in progress and the latest run in its attributes.

A run is only scored when it reached the target. A changed target, missing data or a run far
longer than predicted closes it unscored. A run counts as *clean* when the zone was heating for
at least 80% of it: a shared boiler can stop heating a zone mid-run, which the model cannot
know about, so those runs are kept but do not feed the means. Results survive restarts and are
cleared by the `reset` service.

## Self-calibration

Run scoring also corrects the model. From the clean scored runs the integration keeps a
multiplicative factor on the predicted minutes: the median of `actual / predicted`, shrunk
towards 1.0 as if three extra runs had been exactly right, and clamped to 0.5 to 2.0. It stays
at exactly 1.0 until five clean runs exist, so weak evidence changes nothing.

- `preheat_minutes` publishes the calibrated figure. The whole figure is scaled, dead time
  included, because the runs measure the total warm-up and cannot tell the parts apart.
- Attributes: `calibration_factor`, `calibration_runs` and `uncalibrated_minutes`.
- `preheat_start` (and so `preheat_now`) uses the same figure, so the start agrees with the
  published minutes.
- Scoring always judges the **uncalibrated** prediction, so the factor never feeds back into its
  own evidence; the published figure is stored next to it in each run for reference.
- The evidence is the stored scoring results, so it survives restarts and the `reset` service
  clears it. Turn it off with the `self_calibrate` option (default on).

The factor is a coarse correction: it cannot rescue a model that is wrong by an order of
magnitude (it saturates at 0.5 or 2.0), and the real fix there is a better fit.

## Back-test

`scripts/backtest_runs.py` replays recorded history through the real `RunScorer` and the
calibration, using only data before each run to fit the model (refit at most weekly):

```bash
uv run --managed-python --python 3.13 python scripts/backtest_runs.py history/room_a.jsonl history/room_b.jsonl
```

Input is JSON lines, one row per observation:
`{"ts": "2026-01-05T06:00:00+00:00", "indoor_c": 17.9, "outdoor_c": 4.2, "effort": 0.8}`
(`effort` 0 to 1, any value may be null). History has no thermostat targets, so runs are a
proxy: a heating episode (effort at least 0.5, short pauses bridged) in which the room rose by
at least 1 K, with the target taken as the highest temperature of the episode. That makes the
numbers a relative baseline, not what a user would see. Errors are predicted minus actual.

Baseline on about eleven months of history of six rooms of one house (30 day fit window, clean
runs only). Calibration is applied chronologically, each run using only earlier runs:

| Room | Clean runs | MAE | Bias | MAE calibrated | Bias calibrated |
|---|---|---|---|---|---|
| A | 28 | 10.9 | +2.6 | 11.1 | +0.0 |
| B | 40 | 27.5 | +27.0 | 17.5 | +4.6 |
| C | 32 | 25.3 | +21.7 | 15.4 | +0.4 |
| D | 0 | - | - | - | - |
| E | 6 | 29.0 | +29.0 | 27.8 | +27.8 |
| F | 89 | 41.3 | +21.0 | 39.2 | +10.0 |

(Room D had no usable episodes: its temperature sensor reports whole degrees and does not track
the room. Room E rarely heats.) Before the fit fix (a 5 minute grid and no floor on the robust
scale) the same procedure gave errors of 380 to 980 minutes in rooms A, B, C and F, because the
heating rate came out ten times too low. For comparison, on a synthetic radiator room with a
well-fitted model the procedure takes the MAE from 10.0 to 6.6 minutes. The calibration
removes most of the remaining bias, and never hurts when the evidence is weak, which is why it
is on by default.

### Against a run-level rule

`--baselines` scores the RC model against a rule that learns the minutes directly from
finished runs, `deadtime + mass * gap + loss * (target - outdoor)` (the shape that
Ecronika/ha-preheat learns), with two learners: a gradient step per run and a ridge least
squares. All three are scored on the same runs and learn only from earlier ones:

```bash
uv run --managed-python --python 3.13 python scripts/backtest_runs.py history/room_a.jsonl --baselines
```

On the same history the RC model is clearly better in the room with the most regular heating
(9 minutes against 24 to 31), about equal in two more, and a few minutes worse in room F, whose
target follows presence. The rule has no use for the effort or the cooling between runs, so it
is a useful fallback, not a replacement.

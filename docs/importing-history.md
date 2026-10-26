# Importing history


The recorder keeps little history (10 days by default), but a Prometheus-compatible store
(Prometheus, Mimir, VictoriaMetrics) often holds a year. `scripts/export_prometheus_history.py`
(standard library only) turns it into a JSON-lines file and the service
`adaptive_preheat.import_history` loads it into one zone and refits.

File format, one row per time step, any order, timestamps with a UTC offset (`Z` or `+00:00`):

```json
{"ts": "2026-01-05T07:00:00+00:00", "indoor_c": 18.4, "outdoor_c": 3.1, "effort": 0.0}
```

`indoor_c`, `outdoor_c` and `effort` (0 to 1) may be `null` or absent. `effort` should be 0
while the boiler is off, so gate a valve demand by the boiler or zone state. A file with any
invalid line is rejected as a whole, with the first offending lines named.

Export, for a Prometheus where Home Assistant sensors are exported with an `entity` label
(the metric names and labels below are an example, adapt them to your exporter):

```bash
python scripts/export_prometheus_history.py http://prometheus.example:9009/prometheus \
    --start 2025-12-01 --end 2026-04-01 --step 5m --output /config/history/living_room.jsonl \
    --indoor  'homeassistant_sensor_temperature_celsius{entity="sensor.living_room_temperature"}' \
    --outdoor 'homeassistant_sensor_temperature_celsius{entity="sensor.outdoor_temperature"}' \
    --effort  'homeassistant_sensor_unit_percent{entity="sensor.living_room_valve_demand"} / 100
               * on() group_left max(homeassistant_binary_sensor_state{entity="binary_sensor.heating_zone_on"})'
```

Requests are chunked below 11000 points; each expression must match exactly one series.
Use `--header "Authorization: Bearer ..."` for an authenticated API. Then call the service
(Developer tools, Actions) with `config_entry_id` of the zone (optional with a single zone),
`path` (inside `allowlist_external_dirs`, or the configuration folder; relative paths start
there) and optionally `replace: true`. The default merges like the first-start recorder
import (existing rows win); `replace` discards what was learnt first. The response gives the
rows loaded, the span, and `dropped_older_than_history_days`.

**Set `history_days` first.** The model keeps only the last `history_days` (7 to 400, default
30) before its newest sample and older imported rows are dropped, and the response says how
many. Imported data survives restarts like any other (it is part of the stored model), and the
first-start recorder import merges rather than replaces it. The `retrain` action, however,
reloads the recorder window and *replaces* the model data, so do not run it after an import.

**Recommended options: leave the defaults (30 days, half-life 21 days), unless you want to keep
the data around.** Measured on a year of 5 minute data from five rooms, four in the winter holdouts (refits of the
real model, chronological holdouts: the last three autumn weeks, February, March and April;
skill = 1 - RMSE / persistence RMSE over 60 minute forecasts), fitting on the full year did not
beat fitting on the last 30 days:

| Training data | skill, all holdout windows | skill, windows with heating |
| --- | --- | --- |
| 30 days, half-life 21 d | +0.07 / +0.01 / +0.02 | +0.22 / +0.04 / +0.05 |
| full history, half-life 21 d | +0.01 / +0.01 / +0.02 | +0.11 / +0.04 / +0.03 |
| full history, half-life 60 d | +0.02 / +0.00 / +0.02 | +0.09 / +0.02 / +0.03 |
| full history, half-life 120 d | +0.01 / +0.00 / +0.02 | +0.03 / +0.02 / +0.03 |
| full history, unweighted | +0.00 / +0.00 / +0.02 | +0.02 / +0.02 / +0.03 |

(Mean over rooms for the autumn / March-April / February holdouts.) More heating hours made the
fitted heating rate shrink towards zero, a sign that valve demand is a weak proxy for delivered
heat in these rooms, so more of it does not sharpen the fit. Importing is therefore mainly
useful for rooms that have little recent heating, or to inspect the data; the half-life and
history options did not need a new range for accuracy. The limits only grew so a long import
can be kept. A year of 5 minute rows costs about 30 MB of memory while loading, a 1 to 6 s fit
in the executor (hourly, only when new data arrived), and 0.1 to 0.3 MB in the stored model.

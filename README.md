# Room Thermal Model

A generic Home Assistant integration that **learns how each room heats up and cools
down** from your own history, and tells you **how many minutes of heating a room needs**
to reach a target temperature, so that automations can start heating at the right time.

It works with **any `climate` entity** (TRVs, generic_thermostat, Versatile Thermostat,
smart thermostats…) and only reads standard Home Assistant attributes. It never controls
anything: it only exposes sensors.

This is a fork of [giogandola98/ha-room-thermal-model](https://github.com/giogandola98/ha-room-thermal-model)
with CI, release tooling, strict typing of the core, a coverage gate and [run scoring](#run-scoring)
added. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## How it works

Each zone is modelled as a single thermal mass that gains heat from the heater and loses
it to the outside (a lumped *RC* model), on a 5-minute grid:

```
ΔT/Δt = a · effort(t − dead time) − b · (T_room − T_outdoor) + c
```

| Parameter | Meaning |
|---|---|
| `a` (K/h) | how fast the heater warms the room at full effort |
| `b` (1/h) | heat loss to the outside — time constant τ = 1/b |
| `c` (K/h) | internal and solar gains |
| dead time (min) | lag before the radiator starts warming the room |

- **Learning** uses *all* history, not only preheat sessions: periods with the heater off
  teach heat loss, periods with it on teach heating power. A robust (Huber) weighted
  least-squares fit down-weights anomalies such as an open window, and recent data
  weighs more (half-life 21 days). On first start the recorder history is imported, so
  the model is useful from day one if you have a few days of history.
- **Preheat minutes** come from the exact solution of the model. The heat takes the dead
  time `d` to arrive, so the room first coasts (no heating) for `d` minutes to a
  temperature `T_d`; then `t = d + τ · ln((T∞ − T_d) / (T∞ − T_target))`. `T∞` is the
  highest temperature the room can reach with the heater at full effort. It uses the
  outdoor temperature averaged over the preheat window: from the weather forecast when
  available, otherwise the current outdoor value, otherwise the model's reference
  outdoor temperature. If `T∞` is below the target, the zone reports **unreachable**.
- **Maturity** compares the model's 60-minute-ahead temperature forecasts with what
  really happened and with a naive "temperature stays the same" baseline, and checks
  predicted vs observed heat-up durations.

Ideas for the dead-time detection, cooling-curve cross-check and forecast averaging come
from [Ecronika/ha-preheat](https://github.com/Ecronika/ha-preheat) (MIT, © 2025 Ecronika).

## Installation

HACS → ⋮ → **Custom repositories** → `https://github.com/d0ugal/ha-room-thermal-model`,
category **Integration** → install → restart Home Assistant.
Manual: copy `custom_components/room_thermal_model/` into your config directory.

Requires Home Assistant 2026.1+ and the `recorder` (for the history import).

## Configuration (one entry per zone)

Settings → Devices & services → Add integration → **Room Thermal Model**.

| Input | Required | Notes |
|---|---|---|
| Zone name | yes | used for entity ids |
| Climate entity | yes | `current_temperature`, `hvac_action`, target `temperature`, HVAC mode |
| Room temperature sensor | no | overrides the climate's temperature (e.g. a sensor away from the radiator) |
| Heating effort sensor (%) | no | valve opening / heating demand 0–100 %; otherwise `hvac_action` is used (heating = 100 %, idle/off = 0 %); if the climate has no `hvac_action`, "mode on and target > current" |
| Outdoor temperature sensor | no | current value and history |
| Weather entity | no | forecast for the preheat window (hourly → twice daily → daily) and outdoor fallback |
| Arrival sensor | no | any timestamp sensor (e.g. `sensor.house_next_return_eta` from [Home Return Predictor](https://github.com/giogandola98/ha-home-return-predictor)); enables preheat start / preheat now |

Options: target temperature (fixed, default 20.5 °C, or follow the climate's target),
data half-life (21 days), history window (30 days), update interval (5 min).
Without any outdoor source the model still works but cannot follow the weather, so
expect larger errors.

## Entities (per zone)

| Entity | Content |
|---|---|
| `sensor.<zone>_preheat_minutes` | minutes of full heating needed to reach the target; unknown while learning, when unreachable or without outdoor data (attribute `reason`, e.g. `unreachable`, `no_outdoor`) |
| `sensor.<zone>_max_reachable_temperature` | highest temperature reachable at the outdoor temperature averaged over the preheat window |
| `sensor.<zone>_model_maturity` | 0–100 %; attributes `rmse_c`, `baseline_rmse_c`, `duration_mae_minutes`, `heating_hours`, `cooling_hours` |
| `sensor.<zone>_preheat_start` | latest time to start heating to be at the target on arrival (only with an arrival sensor). The room keeps cooling until then, so it is computed from the temperature the room will have at the start time, not the current one. When the target is unreachable it aims at 0.5 K below the max reachable temperature instead |
| `binary_sensor.<zone>_preheat_now` | on when it is time to start heating (only with an arrival sensor). It latches: once on it stays on until the arrival, unless the arrival moves more than (needed minutes + 30 min) away |
| `sensor.<zone>_time_constant`, `_heating_rate`, `_dead_time` | diagnostic: learnt parameters |
| `sensor.<zone>_last_run_error`, `_run_mae` | diagnostic: how live predictions turned out (see below) |

When the target is unreachable, `sensor.<zone>_preheat_minutes` stays unknown with reason
`unreachable`, while preheat start and preheat now still work as described above.

The `reason` attribute of the preheat sensors explains why there is no value: for example
`unreachable` (the target is above the max reachable temperature) or `no_outdoor` (the
model needs outdoor data but none is available: no sensor, no forecast and no reference
learnt yet).

Changing the input sensors via **Reconfigure** discards the learnt model and re-imports
the history automatically.

Services: `room_thermal_model.retrain`, `room_thermal_model.reset`,
`room_thermal_model.export_diagnostics`, `room_thermal_model.import_history` (see
[Importing history](#importing-history)). Each accepts an optional `entity_id` (any entity
of the zone, multiple allowed) and an optional `config_entry_id`; a call without a target
acts on every zone. Diagnostics download is supported.

## Importing history

The recorder keeps little history (10 days by default), but a Prometheus-compatible store
(Prometheus, Mimir, VictoriaMetrics) often holds a year. `scripts/export_prometheus_history.py`
(standard library only) turns it into a JSON-lines file and the service
`room_thermal_model.import_history` loads it into one zone and refits.

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

## Example: start heating in time

```yaml
automation:
  - alias: Living room preheat
    triggers:
      - trigger: state
        entity_id: binary_sensor.living_room_preheat_now
        to: "on"
    actions:
      - action: climate.set_preset_mode
        target:
          entity_id: climate.living_room
        data:
          preset_mode: comfort
```

With Home Return Predictor's VTherm blueprint, set the blueprint's "preheat minutes
sensor" input to `sensor.<zone>_preheat_minutes`.

## Development

```bash
uv sync --locked --managed-python --python 3.13
make test       # pure model tests (no HA) + integration tests, 95% branch coverage gate
make lint       # ruff
make simulate   # synthetic rooms: learning curve
```

The model core (`rtm_core/`) is pure standard-library Python behind a `ThermalModel`
interface, so it can be tested alone or replaced by another backend. Setup, CI, releases and
deployment are described in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Limitations

- One thermal mass per zone: adjacent rooms heating each other and strong solar gains
  are only captured on average.
- The heating effort from `hvac_action` is on/off; a real valve-opening sensor gives
  more precise results.
- Without an outdoor source, preheat estimates cannot adapt to the weather.

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

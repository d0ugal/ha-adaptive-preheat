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
it to the outside (a lumped *RC* model), on a 15-minute grid (coarser than the sample rate on purpose, see [Fit trust](#fit-trust)):

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
| Schedule | no | a `schedule` helper (e.g. `schedule.living_room_heating`); its next block that raises the target acts as the arrival, see [Schedule-aware preheat](#schedule-aware-preheat) |
| Schedule target key | no | data field of each schedule block holding the target (default `target_temperature`) |

Options: target temperature (fixed, default 20.5 °C, or follow the climate's target),
data half-life (21 days), history window (30 days), update interval (5 min), self-calibration (on, see [below](#self-calibration)), and
`allow_untrusted` (off; see [Fit trust](#fit-trust)).
Without any outdoor source the model still works but cannot follow the weather, so
expect larger errors.
Optional: a zone group (see [Zone groups](#zone-groups)).

## Zone groups

Optional, off by default. Rooms heated by the same boiler zone usually have similar radiators
and the same boiler lag. Give them the same **Zone group** (options flow, free text such as
`upstairs`; compared case-insensitively) and a room whose own model is still immature borrows
the median heating rate `a` and loss rate `b` of its mature peers as the prior of its fit,
instead of the generic radiator defaults (3 K/h, 0.15 1/h). `c` and the dead time are never
shared. Groups only exist within one Home Assistant instance.

Rules:

- a peer lends its parameters only when its own maturity is at least 30 %;
- a room borrows only while its own maturity is below 30 %, so a well fitted room keeps
  learning from its own data alone;
- the prior weighs 12 hours of data per peer, at most 24 (the default prior weighs 6), so
  the room's own data takes over as it accumulates;
- with no usable peer the default prior is used. The prior is refreshed before every fit;
- the maturity sensor shows `zone_group`, `prior_source` (`default` or `group`) and
  `prior_peers`; the fit summary in the diagnostics records the prior (`a`, `b`, hours and
  peers).

For a house with one zone upstairs and one downstairs, set `upstairs` on every room of the
upper floor and leave the downstairs room without a group (a group of one has no peers).

### Evidence

Tested leave-one-room-out on a year of real data (five rooms of one boiler zone, 5 minute
rows): every room fitted with the default prior and with a prior from the other four, on five
30-day windows spread over the heating season, scored on the following 14 days. The score is
the 60-minute-ahead open-loop RMSE divided by the persistence RMSE (below 1 is better).

| Room | Default prior | Peer `a`,`b`, 24 h | Peer `b` only, 24 h |
|---|---|---|---|
| A (rarely heats) | 0.984 | 0.955 | 0.924 |
| C | 1.001 | 1.002 | 0.994 |
| D (heats a lot) | 0.971 | 0.970 | 0.961 |
| E (almost never heats) | 1.005 | 1.013 | 1.008 |

Over all 20 room and window pairs the mean ratio was 0.990 (default), 0.985 (peer `a`,`b`,
24 h) and 0.972 (peer `b`, 24 h); at 6 h weight 0.992, at 48 h 0.979; sharing the dead time as
well gave 0.983. Full-year fits were no different (0.993 default against 0.989 to 0.994 for
every variant). The differences are one to two percent of the persistence error, within the
noise between windows, and the best variant ("`b` only") just pins `b` to the lower bound
that every peer had already hit.

The reason is that none of the 36 default fits was mature: the best reached 11 % maturity,
and 32 of them ended with `b` at its lower bound (a 100 hour time constant), so there was
nothing trustworthy to share. With the 30 % peer threshold above, no peer would have qualified
on this data and every room would have kept the default prior. Sharing does not measurably
help while no room has a good fit; it is meant for the case where one room has learnt well and
its neighbours have not. Nothing in the data shows that it helps there either, which is why it
is opt-in.

## Entities (per zone)

| Entity | Content |
|---|---|
| `sensor.<zone>_preheat_minutes` | minutes of full heating needed to reach the target; unknown while learning, when the fit is not trusted, when unreachable or without outdoor data (attribute `reason`, e.g. `untrusted_fit`, `unreachable`, `no_outdoor`; attributes `trusted`, `trust_reasons`) |
| `sensor.<zone>_max_reachable_temperature` | highest temperature reachable at the outdoor temperature averaged over the preheat window |
| `sensor.<zone>_model_maturity` | 0–100 %; attributes `rmse_c`, `baseline_rmse_c`, `duration_mae_minutes`, `heating_hours`, `cooling_hours`, `trusted`, `trust_reasons` |
| `sensor.<zone>_preheat_start` | latest time to start heating to be at the target on arrival (only with an arrival sensor or a schedule). The room keeps cooling until then, so it is computed from the temperature the room will have at the start time, not the current one. When the target is unreachable it aims at 0.5 K below the max reachable temperature instead |
| `binary_sensor.<zone>_preheat_now` | on when it is time to start heating (only with an arrival sensor or a schedule). It latches: once on it stays on until the arrival, unless the arrival moves more than (needed minutes + 30 min) away |
| `sensor.<zone>_time_constant`, `_heating_rate`, `_dead_time` | diagnostic: learnt parameters |
| `sensor.<zone>_last_run_error`, `_run_mae` | diagnostic: how live predictions turned out (see below) |

When the target is unreachable, `sensor.<zone>_preheat_minutes` stays unknown with reason
`unreachable`, while preheat start and preheat now still work as described above.

The `reason` attribute of the preheat sensors explains why there is no value: for example
`untrusted_fit` (see [Fit trust](#fit-trust)), `unreachable` (the target is above the max reachable temperature) or `no_outdoor` (the
model needs outdoor data but none is available: no sensor, no forecast and no reference
learnt yet).

## Fit trust

The fit always produces numbers, even when the data cannot identify them: a time constant
pinned at its 100 h limit, a heating rate that never left its starting value, or a maximum
reachable temperature of 69 °C. Publishing minutes from such a fit is worse than publishing
nothing, so every fit is assessed (`rtm_core/trust.py`). When it is not trusted,
`sensor.<zone>_preheat_minutes` is unknown with `reason: untrusted_fit`, and preheat start
and preheat now are unknown too (no estimate, nothing is scored). The parameter sensors and
the maximum reachable temperature still publish so you can see what was learnt.

Read `trusted` (true or false) and `trust_reasons` on `preheat_minutes` and
`model_maturity`. Each reason is a machine readable code:

| Reason | Meaning | Threshold |
|---|---|---|
| `loss_rate_at_bound` | the loss rate hit its fast limit, so the data did not identify it (a slow loss, 100 h or more, is a well insulated house and is accepted) | loss rate within 5 % of the upper limit |
| `heating_rate_low` | heating rate implausibly low (typical radiators give 1 to 4 K/h) | below 0.5 K/h |
| `heating_rate_unlearnt` | heating rate is still the starting value | within 0.1 K/h of 3 K/h with under 10 h of heating |
| `dead_time_at_bound` | the dead time search ran to the end of its range | 0 min, or within one step of the maximum (90 min) |
| `max_reachable_implausible` | the room could be heated further above outdoor than any heating system sized for it | (a + c) / b above 3 x (target - (-3 °C)), 70 K at a 20.5 °C target; not judged when the time constant is a day or more |
| `too_little_heating` | not enough heating seen to learn the response | under 10 h at effort 0.5 or more |
| `not_evaluated` | no holdout evaluation yet | too little data |
| `no_skill` | the holdout had heat-up episodes but the 60 minute forecast is not at least 3 % better than "temperature unchanged" | RMSE above 97 % of the baseline |

The maximum reachable limit comes from the target: a heating system is sized to hold the
target on a design cold day (about -3 °C), and a margin of 3 x that is generous. A maximum
reachable temperature below the target is not suspicious; it is the genuine `unreachable`
status.

Evidence from a year of real data in six rooms: in earlier versions every fit tripped at least two
checks. Two causes were found and fixed. The fit regresses on the temperature change over one
grid step; on a 5 minute grid a sensor that moves in 0.1 K steps buries it in noise and the
robust scale (taken from the mostly idle residuals) then treated every heating row as an
outlier, so the heating rate came out ten times too small. The model now runs on a 15 minute
grid and the scale has a floor at the sensor's resolution. Separately, a loss time constant
at its slow limit (100 h or more) is what a well insulated house looks like, so it no
longer fails the check. Back-tested through the run scorer, the mean preheat error fell from
several hours to tens of minutes in the rooms that heat regularly. Fits with few heating
hours still lean on the starting values and stay untrusted until enough heating is seen.

Set the option `allow_untrusted` to publish the estimate anyway. `trusted` and `trust_reasons`
are still reported, so a template or a dashboard can show a warning.

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

## Example: start heating in time (arrival sensor)

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

## Schedule-aware preheat

`preheat_minutes` answers "how long to reach the target from now". Often the real question
is "when must heating start so the room is at the next block's target when that block
begins". Point the zone at a Home Assistant `schedule` helper and it answers that without an
arrival sensor.

Give each block of the schedule a data field with the target, for example
`target_temperature: 21` (the field name is configurable). Example for a room with the
schedule `schedule.living_room_heating`:

| Block | Data |
|---|---|
| Mon-Fri 06:30-08:30 | `target_temperature: 21` |
| Mon-Fri 17:00-22:00 | `target_temperature: 21` |

At 05:00 on a weekday with the room at 17 C, the next rise is 06:30 at 21 C. If the model
needs 70 minutes from the temperature the room will have by then, `preheat_start` is
about 05:20 and `binary_sensor.living_room_preheat_now` turns on at that moment, stays on
until 06:30, and then moves on to the next block (17:00).

How it works:

- the schedule is read with `schedule.get_schedule` at most every 5 minutes and whenever the
  schedule entity changes state; if the service fails this is logged once and the zone
  behaves as without a schedule;
- the next rise is the first block start within 24 hours whose target is more than 0.5 K above
  the target in force before it. Inside a block, a block that starts right after it is compared
  with it; after a gap (or outside any block) it is compared with the zone's current target,
  or, when inside a block, any block counts. Blocks that run to midnight, days without blocks,
  DST changes (wall-clock times in Home Assistant's time zone) and malformed blocks are handled;
- the lead time is computed for the rise's own target, not the room's current one;
- `preheat_minutes` is unchanged (minutes to the current target) and gains the attribute
  `next_block_target_c`. `preheat_start` and `preheat_now` carry `arrival`,
  `arrival_source` (`schedule` or `sensor`), `arrival_target_c`, and `preheat_start` also
  `next_block_start` and `next_block_target_c`;
- with both an arrival sensor and a schedule, the sooner arrival wins (a sensor arrival aims
  at the zone's current target); the schedule's next block is still reported as attributes.

```yaml
automation:
  - alias: Living room schedule preheat
    triggers:
      - trigger: state
        entity_id: binary_sensor.living_room_preheat_now
        to: "on"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: "{{ state_attr('sensor.living_room_preheat_start', 'next_block_target_c') }}"
```

The heating then reaches the block's target as the block begins, when the schedule takes over.

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

### Back-test

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

#### Against a run-level rule

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

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
data half-life (21 days), history window (30 days), update interval (5 min), and
`allow_untrusted` (off; see [Fit trust](#fit-trust)).
Without any outdoor source the model still works but cannot follow the weather, so
expect larger errors.

## Entities (per zone)

| Entity | Content |
|---|---|
| `sensor.<zone>_preheat_minutes` | minutes of full heating needed to reach the target; unknown while learning, when the fit is not trusted, when unreachable or without outdoor data (attribute `reason`, e.g. `untrusted_fit`, `unreachable`, `no_outdoor`; attributes `trusted`, `trust_reasons`) |
| `sensor.<zone>_max_reachable_temperature` | highest temperature reachable at the outdoor temperature averaged over the preheat window |
| `sensor.<zone>_model_maturity` | 0–100 %; attributes `rmse_c`, `baseline_rmse_c`, `duration_mae_minutes`, `heating_hours`, `cooling_hours`, `trusted`, `trust_reasons` |
| `sensor.<zone>_preheat_start` | latest time to start heating to be at the target on arrival (only with an arrival sensor). The room keeps cooling until then, so it is computed from the temperature the room will have at the start time, not the current one. When the target is unreachable it aims at 0.5 K below the max reachable temperature instead |
| `binary_sensor.<zone>_preheat_now` | on when it is time to start heating (only with an arrival sensor). It latches: once on it stays on until the arrival, unless the arrival moves more than (needed minutes + 30 min) away |
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
| `loss_rate_at_bound` | the loss rate hit its limit, so the data did not identify it | time constant 95 h or more, or loss rate within 5 % of the upper limit |
| `heating_rate_low` | heating rate implausibly low (typical radiators give 1 to 4 K/h) | below 0.5 K/h |
| `heating_rate_unlearnt` | heating rate is still the starting value | within 0.1 K/h of 3 K/h with under 10 h of heating |
| `dead_time_at_bound` | the dead time search ran to the end of its range | 0 min, or within one step of the maximum (90 min) |
| `max_reachable_implausible` | the room could be heated further above outdoor than any heating system sized for it | (a + c) / b above 2 x (target - (-3 °C)), 47 K at a 20.5 °C target |
| `too_little_heating` | not enough heating seen to learn the response | under 10 h at effort 0.5 or more |
| `not_evaluated` | no holdout evaluation yet | too little data |
| `no_skill` | the holdout had heat-up episodes but the 60 minute forecast is not at least 3 % better than "temperature unchanged" | RMSE above 97 % of the baseline |

The maximum reachable limit comes from the target: a heating system is sized to hold the
target on a design cold day (about -3 °C), and a margin of 2 x that is generous. A maximum
reachable temperature below the target is not suspicious; it is the genuine `unreachable`
status.

Evidence from a year of real data in six rooms: with the default 30 day window and with the
whole year, every fit tripped at least two checks. Fits with few heating hours lean on the
starting values; fits with plenty (up to 245 h) still pinned the loss rate and gave a heating
rate under 0.5 K/h with no skill over persistence, so the data did not support the model.
Synthetic rooms with two weeks of data pass all checks.

Set the option `allow_untrusted` to publish the estimate anyway. `trusted` and `trust_reasons`
are still reported, so a template or a dashboard can show a warning.

Changing the input sensors via **Reconfigure** discards the learnt model and re-imports
the history automatically.

Services: `room_thermal_model.retrain`, `room_thermal_model.reset`,
`room_thermal_model.export_diagnostics`. Each accepts an optional `entity_id` (any entity
of the zone, multiple allowed) and an optional `config_entry_id`; a call without a target
acts on every zone. Diagnostics download is supported.

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

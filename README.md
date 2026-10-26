# Adaptive Preheat

A Home Assistant integration that **learns how each room heats up and cools down** from your
own history, and tells you **how many minutes of heating a room needs** to reach a target
temperature, so your automations can start heating at the right time.

It works with **any `climate` entity** (TRVs, generic_thermostat, Versatile Thermostat, smart
thermostats) and only reads standard Home Assistant attributes. It **never controls
anything**: it exposes sensors, and what you do with them is up to you.

What sets it apart:

- **It marks its own homework.** Every heat-up is scored (predicted minutes against what really
  happened), and a rolling error and a self-correcting factor are published next to the
  estimate. See [Run scoring](docs/run-scoring.md).
- **It says when not to trust it.** A fit that the data cannot support is withheld instead of
  published as a confident number. See [Fit trust](docs/fit-trust.md).
- **It copes with real sensors.** The fit is robust to open windows and to room sensors that
  only move in 0.1 K steps, and it handles slow-losing, well insulated houses.
- **Schedule aware.** Point a zone at a `schedule` helper and it works out when heating must
  start for the next block, with no arrival sensor. See
  [Arrival sensor and schedule](docs/schedule-and-arrival.md).
- **Your history, not just the recorder's.** A year of Prometheus-style history can be imported
  to start from. See [Importing history](docs/importing-history.md).

> **Upgrading from `room_thermal_model` (before 0.6.0):** the integration domain is now
> `adaptive_preheat`. Delete each zone under the old integration first (otherwise the new
> entities get a `_2` suffix), remove the old `custom_components/room_thermal_model/`
> directory, restart Home Assistant and add the zones again. Learnt models are not carried
> over; use `import_history` to retrain at once. Entity ids (`sensor.<zone>_<key>`) are
> unchanged, but services are now `adaptive_preheat.*`.

## Installation

HACS → ⋮ → **Custom repositories** → `https://github.com/d0ugal/ha-adaptive-preheat`,
category **Integration** → install → restart Home Assistant.
Manual: copy `custom_components/adaptive_preheat/` into your config directory.

Requires Home Assistant 2026.1+ and the `recorder` (for the history import).

## Add a zone

Settings → Devices & services → Add integration → **Adaptive Preheat**, one entry per zone
(a room or a group of rooms heated together). Only the zone name and the climate entity are
required. Better inputs give a better model:

| Input | Why you would add it |
|---|---|
| Room temperature sensor | a sensor away from the radiator beats the climate's own reading |
| Heating effort sensor (%) | valve opening or heating demand; far better than on/off `hvac_action` |
| Outdoor temperature sensor and weather entity | the model follows the weather and averages the forecast over the preheat window |
| Arrival sensor or schedule | turns "minutes from now" into "start heating at HH:MM" |

All inputs and options are in [Configuration](docs/configuration.md).

## Entities (per zone)

| Entity | Content |
|---|---|
| `sensor.<zone>_preheat_minutes` | minutes of full heating needed to reach the target; unknown while learning, when the fit is not trusted, when unreachable or without outdoor data (attribute `reason`; attributes `trusted`, `trust_reasons`) |
| `sensor.<zone>_max_reachable_temperature` | highest temperature reachable at the outdoor temperature averaged over the preheat window |
| `sensor.<zone>_model_maturity` | 0 to 100 %; attributes `rmse_c`, `baseline_rmse_c`, `duration_mae_minutes`, `heating_hours`, `cooling_hours`, `trusted`, `trust_reasons` |
| `sensor.<zone>_preheat_start` | latest time to start heating to be at the target on arrival (only with an arrival sensor or a schedule); computed from the temperature the room will have at the start time, not the current one |
| `binary_sensor.<zone>_preheat_now` | on when it is time to start heating; latches until the arrival |
| `sensor.<zone>_time_constant`, `_heating_rate`, `_dead_time` | diagnostic: learnt parameters |
| `sensor.<zone>_last_run_error`, `_run_mae` | diagnostic: how live predictions turned out |

The `reason` attribute of the preheat sensors says why there is no value: `untrusted_fit`,
`unreachable` (the target is above the maximum reachable temperature) or `no_outdoor`.

## Use it

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

No arrival sensor? Read `sensor.living_room_preheat_minutes` in your own template, or give the
zone a schedule, see [Arrival sensor and schedule](docs/schedule-and-arrival.md).

## How it works

Each zone is modelled as a single thermal mass that gains heat from the heater and loses it to
the outside (a lumped *RC* model), on a 15 minute grid (coarser than the sample rate on
purpose, see [Fit trust](docs/fit-trust.md)):

```
ΔT/Δt = a · effort(t − dead time) − b · (T_room − T_outdoor) + c
```

| Parameter | Meaning |
|---|---|
| `a` (K/h) | how fast the heater warms the room at full effort |
| `b` (1/h) | heat loss to the outside; time constant τ = 1/b |
| `c` (K/h) | internal and solar gains |
| dead time (min) | lag before the radiator starts warming the room |

- **Learning** uses *all* history, not only preheat sessions: periods with the heater off teach
  heat loss, periods with it on teach heating power. A robust (Huber) weighted least-squares
  fit down-weights anomalies such as an open window, and recent data weighs more (half-life
  21 days). On first start the recorder history is imported, so the model is useful from day
  one if you have a few days of it.
- **Preheat minutes** come from the exact solution of the model. The heat takes the dead time
  `d` to arrive, so the room first coasts for `d` minutes to a temperature `T_d`; then
  `t = d + τ · ln((T∞ − T_d) / (T∞ − T_target))`. `T∞` is the highest temperature the room can
  reach with the heater at full effort, using the outdoor temperature averaged over the
  preheat window. If `T∞` is below the target, the zone reports **unreachable**.
- **Maturity** compares the model's 60 minute forecasts with what really happened and with a
  naive "temperature stays the same" baseline.

## Can I trust the numbers?

Three layers, each documented on its own page:

1. **[Fit trust](docs/fit-trust.md):** every fit is checked (heating rate learnt, dead time
   inside its range, skill over "no change", enough heating seen). A fit that fails is
   withheld rather than published.
2. **[Run scoring](docs/run-scoring.md):** each heat-up is scored against the prediction made
   when it began. `run_mae` is the mean absolute error in minutes.
3. **[Self-calibration](docs/run-scoring.md#self-calibration):** after five clean runs the
   published minutes are scaled by the median of actual over predicted, shrunk toward 1.0.

On a year of recorded history from one house the back-test gives a mean preheat error of about
11 minutes in the room that heats most regularly, and 15 to 40 minutes in the others once calibrated, and the
[back-test](docs/run-scoring.md#back-test) script lets you measure your own.

## Limitations

- One thermal mass per zone: adjacent rooms heating each other and strong solar gains are only
  captured on average.
- The heating effort from `hvac_action` is on/off; a real valve-opening sensor gives more
  precise results.
- Without an outdoor source, preheat estimates cannot adapt to the weather.
- It needs some heating to learn from. In a mild spell it reports `untrusted_fit` until enough
  heating has been seen.

## Documentation

| Page | Contents |
|---|---|
| [Configuration](docs/configuration.md) | every input and option, services, reconfiguring |
| [Arrival sensor and schedule](docs/schedule-and-arrival.md) | start heating at the right time |
| [Fit trust](docs/fit-trust.md) | the checks, their thresholds and the evidence behind them |
| [Run scoring](docs/run-scoring.md) | scoring, self-calibration, the back-test and the comparison with a run-level rule |
| [Importing history](docs/importing-history.md) | export from Prometheus and load a year of data |
| [Zone groups](docs/zone-groups.md) | optional shared priors for rooms on one boiler zone |
| [Development](docs/DEVELOPMENT.md) | setup, CI, releases and deployment |
| [Original plan](docs/PLAN.md) | the first design |

## Hat tips

This project stands on other people's work, and we are grateful for it.

- **[giogandola98/ha-room-thermal-model](https://github.com/giogandola98/ha-room-thermal-model)**
  is where it all began. This repository started as a fork of it and keeps its core idea: a
  small RC model per zone, learnt from the recorder, that exposes sensors and controls
  nothing. The first model, the config flow and the structure are theirs. (MIT)
- **[Ecronika/ha-preheat](https://github.com/Ecronika/ha-preheat)** gave us several ideas:
  detecting the dead time from the slope after the heating switches on, a cooling-curve
  cross-check on the time constant, averaging the weather forecast over the preheat window,
  and learning from finished heat-ups, which we re-implemented as the run-level rule we
  measure the model against. Its habit of guarding what the model learns from (skip tiny
  rises, skip when the valve barely opened) keeps shaping ours. (MIT, © 2025 Ecronika)
- **[giogandola98/ha-home-return-predictor](https://github.com/giogandola98/ha-home-return-predictor)**
  is the companion this integration was built to work with: its arrival sensor, and its
  Versatile Thermostat blueprint, are the natural way to start heating on time.

Anything in this repository that is borrowed says so next to the code (see
`rtm_core/deadtime.py`, `cooling.py` and `rule_model.py`).

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

## Licence

MIT, see [LICENSE](LICENSE).

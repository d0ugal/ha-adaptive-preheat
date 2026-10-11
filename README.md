# Room Thermal Model

A generic Home Assistant integration that **learns how each room heats up and cools
down** from your own history, and tells you **how many minutes of heating a room needs**
to reach a target temperature, so that automations can start heating at the right time.

It works with **any `climate` entity** (TRVs, generic_thermostat, Versatile Thermostat,
smart thermostats…) and only reads standard Home Assistant attributes. It never controls
anything: it only exposes sensors.

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
- **Preheat minutes** come from the exact solution of the model:
  `t = dead time + τ · ln((T∞ − T_now) / (T∞ − T_target))`, where `T∞` is the highest
  temperature the room can reach at the current outdoor temperature. If the weather
  entity provides a forecast, the outdoor temperature is averaged over the preheat
  window. If `T∞` is below the target, the zone reports **unreachable**.
- **Maturity** compares the model's 60-minute-ahead temperature forecasts with what
  really happened and with a naive "temperature stays the same" baseline, and checks
  predicted vs observed heat-up durations.

Ideas for the dead-time detection, cooling-curve cross-check and forecast averaging come
from [Ecronika/ha-preheat](https://github.com/Ecronika/ha-preheat) (MIT, © 2025 Ecronika).

## Installation

HACS → ⋮ → **Custom repositories** → `https://github.com/giogandola98/ha-room-thermal-model`,
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
| `sensor.<zone>_preheat_minutes` | minutes of full heating needed to reach the target; unknown while learning or when unreachable (attribute `reason`) |
| `sensor.<zone>_max_reachable_temperature` | highest temperature reachable at the current outdoor temperature |
| `sensor.<zone>_model_maturity` | 0–100 %; attributes `rmse_c`, `baseline_rmse_c`, `duration_mae_minutes`, `heating_hours`, `cooling_hours` |
| `sensor.<zone>_preheat_start` | arrival − preheat minutes (only with an arrival sensor) |
| `binary_sensor.<zone>_preheat_now` | on when it is time to start heating (only with an arrival sensor) |
| `sensor.<zone>_time_constant`, `_heating_rate`, `_dead_time` | diagnostic: learnt parameters |

Services: `room_thermal_model.retrain`, `room_thermal_model.reset`,
`room_thermal_model.export_diagnostics`. Diagnostics download is supported.

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
pip install -r requirements_test.txt
pytest                     # pure model tests (no HA) + integration tests
python scripts/simulate.py --days 30   # synthetic rooms: learning curve
```

The model core (`rtm_core/`) is pure standard-library Python behind a `ThermalModel`
interface, so it can be tested alone or replaced by another backend.

## Limitations

- One thermal mass per zone: adjacent rooms heating each other and strong solar gains
  are only captured on average.
- The heating effort from `hvac_action` is on/off; a real valve-opening sensor gives
  more precise results.
- Without an outdoor source, preheat estimates cannot adapt to the weather.

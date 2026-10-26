# Configuration

One config entry per zone. Everything except the zone name and the climate entity is optional.


## Inputs

Settings → Devices & services → Add integration → **Adaptive Preheat**.

| Input | Required | Notes |
|---|---|---|
| Zone name | yes | used for entity ids |
| Climate entity | yes | `current_temperature`, `hvac_action`, target `temperature`, HVAC mode |
| Room temperature sensor | no | overrides the climate's temperature (e.g. a sensor away from the radiator) |
| Heating effort sensor (%) | no | valve opening / heating demand 0–100 %; otherwise `hvac_action` is used (heating = 100 %, idle/off = 0 %); if the climate has no `hvac_action`, "mode on and target > current" |
| Outdoor temperature sensor | no | current value and history |
| Weather entity | no | forecast for the preheat window (hourly → twice daily → daily) and outdoor fallback |
| Arrival sensor | no | any timestamp sensor (e.g. `sensor.house_next_return_eta` from [Home Return Predictor](https://github.com/giogandola98/ha-home-return-predictor)); enables preheat start / preheat now |
| Schedule | no | a `schedule` helper (e.g. `schedule.living_room_heating`); its next block that raises the target acts as the arrival, see [Schedule-aware preheat](schedule-and-arrival.md#schedule-aware-preheat) |
| Schedule target key | no | data field of each schedule block holding the target (default `target_temperature`) |

Options: target temperature (fixed, default 20.5 °C, or follow the climate's target),
data half-life (21 days), history window (30 days), update interval (5 min), self-calibration (on, see [run scoring](run-scoring.md#self-calibration)), and
`allow_untrusted` (off; see [Fit trust](fit-trust.md)).
Without any outdoor source the model still works but cannot follow the weather, so
expect larger errors.
Optional: a zone group (see [Zone groups](zone-groups.md)).

## Services and reconfiguring

Changing the input sensors via **Reconfigure** discards the learnt model and re-imports
the history automatically.

Services: `adaptive_preheat.retrain`, `adaptive_preheat.reset`,
`adaptive_preheat.export_diagnostics`, `adaptive_preheat.import_history` (see
[Importing history](importing-history.md)). Each accepts an optional `entity_id` (any entity
of the zone, multiple allowed) and an optional `config_entry_id`; a call without a target
acts on every zone. Diagnostics download is supported.

# Arrival sensor and schedule

Two ways to tell the zone when you want the room warm. Both are optional; without them you
still get `preheat_minutes` (how long from now).


## Arrival sensor
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

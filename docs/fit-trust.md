# Fit trust


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

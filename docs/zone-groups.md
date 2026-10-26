# Zone groups


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

## Evidence

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

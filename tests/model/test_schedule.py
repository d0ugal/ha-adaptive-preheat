"""Pure tests of the schedule rise finder (stdlib only)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from rtm_core.schedule import Block, Rise, next_rise, parse_blocks

LONDON = ZoneInfo("Europe/London")
KEY = "target_temperature"


def block(frm: str, to: str, target: float | str | None = 20.0, **extra: object) -> dict:
    data: dict = {} if target is None else {KEY: target}
    return {"from": frm, "to": to, "data": data, **extra}


def raw(**days: list[dict]) -> dict:
    return days


def at(day: int, hh: int, mm: int = 0, tz: ZoneInfo = LONDON, month: int = 3) -> datetime:
    # 2026-03-02 is a Monday
    return datetime(2026, month, day, hh, mm, tzinfo=tz)


def rise(schedule: dict, now: datetime, current: float | None = 16.0, **kw) -> Rise | None:
    return next_rise(parse_blocks(schedule, KEY), now, current, **kw)


# ------------------------------------------------------------------ parsing


def test_parse_reads_weekdays_times_and_targets() -> None:
    blocks = parse_blocks(
        raw(monday=[block("06:30:00", "08:00:00", 21)], sunday=[block("07:00", "09:00", "19.5")]),
        KEY,
    )
    assert blocks == [Block(0, 23400, 28800, 21.0), Block(6, 25200, 32400, 19.5)]


@pytest.mark.parametrize("end", ["24:00:00", "00:00:00", time.max])
def test_block_ending_at_midnight(end: object) -> None:
    (parsed,) = parse_blocks(raw(monday=[block("22:00:00", end, 20)]), KEY)  # type: ignore[arg-type]
    assert parsed.end_s == 86400


def test_time_objects_are_accepted() -> None:
    entry = {"from": time(6, 0), "to": time(7, 0), "data": {KEY: 20}}
    assert parse_blocks(raw(monday=[entry]), KEY) == [Block(0, 21600, 25200, 20.0)]


@pytest.mark.parametrize(
    "entry",
    [
        "nonsense",
        {"from": "06:00:00", "to": "07:00:00"},  # no data
        {"from": "06:00:00", "to": "07:00:00", "data": "x"},
        {"from": "06:00:00", "to": "07:00:00", "data": {}},  # key missing
        {"from": "06:00:00", "to": "07:00:00", "data": {KEY: "warm"}},
        {"from": "06:00:00", "to": "07:00:00", "data": {KEY: float("nan")}},
        {"from": "06:00:00", "to": "07:00:00", "data": {KEY: True}},
        {"from": "06:00:00", "to": "07:00:00", "data": {KEY: None}},
        {"from": "bad", "to": "07:00:00", "data": {KEY: 20}},
        {"from": "06:00:00", "to": 7, "data": {KEY: 20}},
        {"from": "06:61:00", "to": "07:00:00", "data": {KEY: 20}},
        {"from": "06:00:00:00", "to": "07:00:00", "data": {KEY: 20}},
        {"from": "25:00:00", "to": "26:00:00", "data": {KEY: 20}},
        {"from": "08:00:00", "to": "07:00:00", "data": {KEY: 20}},  # reversed
        {"from": "24:00:00", "to": "24:00:00", "data": {KEY: 20}},
    ],
)
def test_malformed_blocks_are_skipped(entry: object) -> None:
    assert parse_blocks(raw(monday=[entry]), KEY) == []  # type: ignore[list-item]


def test_malformed_containers_are_skipped() -> None:
    assert parse_blocks(None, KEY) == []
    assert parse_blocks("x", KEY) == []
    assert parse_blocks({"monday": "x", "funday": [block("06:00", "07:00")]}, KEY) == []


def test_custom_key() -> None:
    entry = {"from": "06:00:00", "to": "07:00:00", "data": {"temp": 18}}
    assert parse_blocks(raw(monday=[entry]), "temp")[0].target_c == 18.0
    assert parse_blocks(raw(monday=[entry]), KEY) == []


# ------------------------------------------------------------------ next rise


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_rise([Block(0, 0, 60, 20.0)], datetime(2026, 3, 2), 16.0)


def test_no_blocks() -> None:
    assert next_rise([], at(2, 5), 16.0) is None


def test_later_today() -> None:
    sched = raw(monday=[block("06:30:00", "08:00:00", 21)])
    assert rise(sched, at(2, 5)) == Rise(at(2, 6, 30), 21.0)


def test_tomorrow_within_the_horizon() -> None:
    sched = raw(tuesday=[block("06:30:00", "08:00:00", 21)])
    assert rise(sched, at(2, 20)) == Rise(at(3, 6, 30), 21.0)


def test_beyond_the_horizon_is_ignored() -> None:
    sched = raw(tuesday=[block("06:30:00", "08:00:00", 21)])
    assert rise(sched, at(2, 5)) is None  # 25.5 h away
    assert rise(sched, at(2, 5), horizon=timedelta(hours=48)) == Rise(at(3, 6, 30), 21.0)


def test_exactly_on_the_horizon_counts() -> None:
    sched = raw(tuesday=[block("05:00:00", "08:00:00", 21)])
    assert rise(sched, at(2, 5)) == Rise(at(3, 5), 21.0)


def test_days_without_blocks_are_skipped() -> None:
    # a room with blocks on only some weekdays
    sched = raw(
        monday=[block("07:00:00", "09:00:00", 20)], thursday=[block("07:00:00", "09:00:00", 20)]
    )
    assert rise(sched, at(3, 12)) is None  # Tuesday noon: next block is Thursday
    assert rise(sched, at(4, 12), horizon=timedelta(days=2)) == Rise(at(5, 7), 20.0)


def test_block_started_now_is_not_future() -> None:
    sched = raw(monday=[block("06:00:00", "08:00:00", 21), block("18:00:00", "22:00:00", 21)])
    assert rise(sched, at(2, 6)) == Rise(at(2, 18), 21.0)


def test_inside_a_block_looks_at_the_next_one() -> None:
    sched = raw(monday=[block("06:00:00", "08:00:00", 21), block("17:00:00", "22:00:00", 21)])
    # after the gap the schedule is off: the evening block is a rise even at equal target
    assert rise(sched, at(2, 7), current=21.0) == Rise(at(2, 17), 21.0)


def test_contiguous_step_up_is_a_rise_and_same_target_is_not() -> None:
    up = raw(monday=[block("06:00:00", "08:00:00", 18), block("08:00:00", "10:00:00", 21)])
    assert rise(up, at(2, 7)) == Rise(at(2, 8), 21.0)
    flat = raw(monday=[block("06:00:00", "08:00:00", 21), block("08:00:00", "10:00:00", 21)])
    assert rise(flat, at(2, 7)) is None


def test_small_step_is_not_a_rise() -> None:
    sched = raw(monday=[block("06:00:00", "08:00:00", 20.0), block("08:00:00", "10:00:00", 20.5)])
    assert rise(sched, at(2, 7)) is None
    sched = raw(monday=[block("06:00:00", "08:00:00", 20.0), block("08:00:00", "10:00:00", 20.6)])
    assert rise(sched, at(2, 7)) == Rise(at(2, 8), 20.6)


def test_outside_a_block_the_current_target_is_the_baseline() -> None:
    sched = raw(monday=[block("08:00:00", "10:00:00", 18)])
    assert rise(sched, at(2, 5), current=17.0) == Rise(at(2, 8), 18.0)
    assert rise(sched, at(2, 5), current=18.0) is None  # already at that target
    assert rise(sched, at(2, 5), current=None) == Rise(at(2, 8), 18.0)


def test_first_rise_skips_lower_blocks() -> None:
    sched = raw(monday=[block("06:00:00", "07:00:00", 17), block("12:00:00", "13:00:00", 21)])
    assert rise(sched, at(2, 5), current=17.0) == Rise(at(2, 12), 21.0)


def test_contiguous_across_midnight() -> None:
    sched = raw(
        monday=[block("22:00:00", "24:00:00", 18)],
        tuesday=[block("00:00:00", "06:00:00", 18), block("06:00:00", "08:00:00", 21)],
    )
    # Monday 23:00 inside the 18 block; the 00:00 block continues it; 06:00 is the rise
    assert rise(sched, at(2, 23)) == Rise(at(3, 6), 21.0)


def test_block_crossing_midnight_is_current_after_midnight() -> None:
    sched = raw(
        monday=[block("22:00:00", "00:00:00", 18)],
        tuesday=[block("00:00:00", "01:00:00", 18), block("05:00:00", "08:00:00", 21)],
    )
    assert rise(sched, at(3, 0, 30)) == Rise(at(3, 5), 21.0)


def test_yesterdays_block_ending_midnight_is_not_current() -> None:
    sched = raw(
        monday=[block("22:00:00", "24:00:00", 21)], tuesday=[block("07:00:00", "08:00:00", 19)]
    )
    assert rise(sched, at(3, 0, 10), current=15.0) == Rise(at(3, 7), 19.0)


def test_week_wraps_sunday_to_monday() -> None:
    sched = raw(monday=[block("06:00:00", "08:00:00", 21)])
    assert rise(sched, at(8, 20)) == Rise(at(9, 6), 21.0)  # Sunday 8 March -> Monday 9


def test_spring_forward_uses_wall_clock() -> None:
    # 2026-03-29 01:00 UTC the clocks go forward in London (Sunday)
    sched = raw(sunday=[block("06:00:00", "08:00:00", 21)])
    now = datetime(2026, 3, 28, 22, 0, tzinfo=LONDON)
    found = rise(sched, now)
    assert found is not None
    assert found.start == datetime(2026, 3, 29, 6, 0, tzinfo=LONDON)
    assert found.start.astimezone(UTC) == datetime(2026, 3, 29, 5, 0, tzinfo=UTC)  # BST
    assert found.start.astimezone(UTC) - now.astimezone(UTC) == timedelta(hours=7)


def test_fall_back_uses_wall_clock() -> None:
    sched = raw(sunday=[block("06:00:00", "08:00:00", 21)])
    now = datetime(2026, 10, 24, 22, 0, tzinfo=LONDON)
    found = rise(sched, now)
    assert found is not None
    assert found.start.astimezone(UTC) == datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
    assert found.start.astimezone(UTC) - now.astimezone(UTC) == timedelta(hours=9)


def test_works_in_a_fixed_utc_zone() -> None:
    sched = raw(monday=[block("06:30:00", "08:00:00", 21)])
    assert rise(sched, at(2, 5, tz=UTC)) == Rise(at(2, 6, 30, tz=UTC), 21.0)  # type: ignore[arg-type]

#!/usr/bin/env python3
"""Learning-curve simulation of the Adaptive Preheat on synthetic rooms.

Standalone: imports only ``rtm_core`` (no Home Assistant). For each synthetic room the
raw samples are fed online with ``add_sample``; every simulated hour ``fit_due`` is
checked and a fit runs when due and at least ``--fit-hours`` have passed since the
previous one (hourly fits for 30 days × 3 rooms would take minutes of CPU).

Per day it prints the learnt parameters, the holdout forecast RMSE vs the persistence
baseline, the heat-up duration MAE, the maturity and the preheat error versus the
simulated truth for a reference scenario (17 → 21 °C at the current outdoor mean).

    python scripts/simulate.py [--days 30] [--fit-hours 24]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "adaptive_preheat")
)

from rtm_core import ModelConfig, RCThermalModel
from rtm_core import synthetic as syn

REF_START_C = 17.0
REF_TARGET_C = 21.0
REPORT_DAYS = (3, 7, 14, 30)


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return "  -  " if v is None else format(v, spec)


def run_room(spec: syn.RoomSpec, days: int, fit_hours: float) -> dict[int, float | None]:
    run = syn.generate(spec, days)
    model = RCThermalModel(ModelConfig())
    print(
        f"\n=== {spec.name}: true a={spec.a} b={spec.b} (tau {1 / spec.b:.1f} h) "
        f"c={spec.c} d={spec.dead_time_minutes:.0f} min, outdoor={spec.has_outdoor}"
    )
    print(
        " day      a      b(tau h)       c     d   rmse/base    durMAE  mat   "
        "preheat pred/true (err)"
    )
    errors: dict[int, float | None] = {}
    next_hour = run.start + timedelta(hours=1)
    last_fit = None
    fit_secs: list[float] = []
    day = 1
    for s in run.samples:
        while s.ts >= next_hour:
            if model.fit_due(next_hour) and (
                last_fit is None or (next_hour - last_fit) >= timedelta(hours=fit_hours)
            ):
                t0 = time.perf_counter()
                model.fit(next_hour)
                fit_secs.append(time.perf_counter() - t0)
                last_fit = next_hour
            if (next_hour - run.start) >= timedelta(days=day):
                errors[day] = _report(model, run, spec, next_hour, day)
                day += 1
            next_hour += timedelta(hours=1)
        model.add_sample(s)
    if day <= days:
        end = run.samples[-1].ts
        if model.fit_due(end):
            model.fit(end)
        errors[day] = _report(model, run, spec, end, day)
    if fit_secs:
        print(
            f"   fits: {len(fit_secs)}, mean {sum(fit_secs) / len(fit_secs):.2f} s, "
            f"max {max(fit_secs):.2f} s"
        )
    return errors


def _report(model: RCThermalModel, run: syn.SyntheticRun, spec: syn.RoomSpec, now, day: int):
    p = model.parameters()
    q = model.quality()
    m = run.minute(now)
    recent = run.outdoor[max(0, m - 1440) : m] or [spec.outdoor_mean]
    tout = sum(recent) / len(recent)
    truth = syn.true_preheat_minutes(spec, REF_START_C, REF_TARGET_C, tout)
    err = None
    pred_s = "  -  "
    if p is not None:
        est = model.preheat(now, REF_START_C, REF_TARGET_C, tout if spec.has_outdoor else None)
        if est.minutes is not None and truth is not None:
            err = est.minutes - truth
            pred_s = f"{est.minutes:5.0f}/{truth:4.0f} ({err:+5.1f})"
        print(
            f" {day:3d} {p.heating_rate_k_per_h:6.2f} {p.loss_rate_per_h:6.3f}"
            f"({p.time_constant_hours:5.1f}) {p.gains_k_per_h:6.2f} {p.dead_time_minutes:5.0f}"
            f"  {_fmt(q.rmse_c)}/{_fmt(q.baseline_rmse_c)}"
            f"  {_fmt(q.duration_mae_minutes, '6.1f')}"
            f"  {q.maturity:4.2f}   {pred_s}"
        )
    else:
        print(f" {day:3d}  (not fitted yet)")
    return err


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument(
        "--fit-hours",
        type=float,
        default=24.0,
        help="minimum hours between fits (fit_due is checked hourly)",
    )
    args = ap.parse_args()
    t0 = time.perf_counter()
    rooms = [syn.radiator_room(), syn.underfloor_room(), syn.no_outdoor_room()]
    results = {spec.name: run_room(spec, args.days, args.fit_hours) for spec in rooms}
    print("\n=== summary: preheat error vs truth (17 -> 21 C), minutes")
    days = [d for d in REPORT_DAYS if d <= args.days]
    print(" room            " + "".join(f"  day {d:<3d}" for d in days))
    for name, errs in results.items():
        row = "".join(f"  {_fmt(errs.get(d), '+7.1f')}" for d in days)
        print(f" {name:<15s}{row}")
    print(f"\ntotal runtime {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()

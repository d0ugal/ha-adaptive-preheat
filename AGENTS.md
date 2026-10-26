# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the real file; `CLAUDE.md` is a symlink to it. Edit `AGENTS.md`.

## What this is

A Home Assistant custom integration (`custom_components/room_thermal_model`) that learns a lumped RC thermal model per zone from recorder history and exposes sensors, chiefly minutes of heating needed to reach a target. It never controls anything. This is the `d0ugal` fork of `giogandola98/ha-room-thermal-model`; `docs/PLAN.md` is the original design, `docs/DEVELOPMENT.md` covers CI, releases and deployment, `README.md` the model maths and entities.

## Commands

Everything runs through uv. The Makefile pins `--managed-python --python 3.13`; do the same, because the system Python has no `Python.h` for some dependencies.

```bash
uv sync --locked --managed-python --python 3.13
make test                                   # whole suite, enforces 95% branch coverage
uv run --managed-python --python 3.13 pytest tests/model/test_scoring.py::test_reached_run_is_scored_with_a_signed_error -q   # one test
uv run --managed-python --python 3.13 pytest --cov -q       # coverage report
make lint                                   # ruff check + format check in the pinned ruff container (needs docker)
make format
uvx pyright==1.1.408                        # same version as CI
make simulate                               # synthetic rooms, learning curve (scripts/simulate.py)
```

Run ruff with `--exclude .claude` if you call it outside `make`: agent worktrees live under `.claude/` and ruff walks into them.

## Architecture

Two layers, and the boundary matters:

- `rtm_core/` is pure standard-library Python with no Home Assistant imports. It holds the model (`model.py`, `fit.py`, `physics.py`, `deadtime.py`, `cooling.py`, `forecast.py`, `quality.py`), a synthetic-room generator (`synthetic.py`) and run scoring (`scoring.py`). Everything goes through the `ThermalModel` ABC in `base.py`; `create_model()` in `__init__.py` is the factory. Pyright is **strict** here and basic elsewhere. New learning logic goes in the core so it stays testable without Home Assistant.
- The Home Assistant glue wraps it: one config entry per zone, one `RTMCoordinator` per entry (`coordinator.py`, the largest file), entities in `sensor.py` and `binary_sensor.py`, `inputs.py` turns entity states into `Sample`s, `history.py` bootstraps from the recorder, `storage.py` persists to the HA Store.

Coordinator rules that are easy to break (documented at the top of `coordinator.py`):

- Every model call holds `self._lock`; CPU work (`fit`, `load_history`, `to_dict`, `from_dict`, `reset`) runs in the executor under it; cheap calls run in the loop; the lock is never held around recorder reads or service calls.
- Live samples are queued by state listeners and flushed into the model by one task. Each poll also feeds a sample, and a 5 minute grid timer covers long update intervals.
- The model snapshot (`model.to_dict()`) and the run scorer are saved together in one Store document (`_store_data`). The scorer is separate from the model: it is fed in `_async_update_data` from the estimate and the effort, and `reset` clears both.

Run scoring (`rtm_core/scoring.py`): a run opens on the first estimate made while the zone is heating, closes when the room is within 0.2 K of the target, and is scored as `predicted - actual`. Only runs heating for at least 80% of the time feed the means, because a shared boiler can stop a zone mid-run.

Entities use `translation_key == key`, `unique_id = f"{entry_id}_{key}"` and an explicit `entity_id` of `<platform>.<zone>_<key>`. Adding an entity means a key in `const.py`, translations in `strings.json` and all four of `translations/{en,it,fr,de}.json`, and updating `BASE_SENSORS` and the diagnostic set in `tests/integration/test_init.py`.

## Tests

- `tests/model/` imports `rtm_core` only (see `tests/model/conftest.py`), never the Home Assistant package.
- `tests/integration/` uses `pytest-homeassistant-custom-component`; the `fake_model` fixture swaps in a deterministic `FakeModel`, so tests do not exercise the real fit. `no_history` stops the recorder bootstrap.
- Use `freezer.tick()` then `coordinator.async_refresh()` to move time; a state change also triggers a debounced refresh, so assert on properties of the result rather than on exact wall-clock durations where a refresh could land either side of a tick.

## Privacy

This repository is public. Never commit, push or write into a PR, issue or release anything personal or private: real names of people or rooms, home network addresses or hostnames, locations, email addresses, credentials, real file paths, real entity ids or any real data rows. Use generic names (`living_room`, `bedroom_1`, `192.0.2.1`, `example.com`). Commit with the GitHub noreply identity, never a personal email. The check covers commit authors; GitHub itself stamps the merging user's identity as committer on rebase merges, which cannot be avoided from here. `scripts/check_privacy.py` (the `Privacy` workflow) enforces the generic part; it can also read a denylist of personal terms from a file kept outside the repo, for a local pre-push hook. Report results from real data with anonymised room names.

## Workflow

- Conventional commits (checked in CI; release-please reads them), atomic commits, never push to `main`: branch, PR, wait for green.
- Every change needs a test; coverage below 95% fails.
- Releases are cut by release-please, which bumps `pyproject.toml`, `manifest.json` and the changelog but cannot update `uv.lock`. The `refresh-lock` job in `release-please.yml` runs `uv lock` on the release PR branch; if `uv sync --locked` ever fails on `main` after a release, that job failed and the lock needs a manual `uv lock` PR.
- Actions are pinned by SHA. The compatibility matrix maps `pytest-homeassistant-custom-component` versions to Home Assistant versions; the mapping and how to bump the minimum are in the header of `.github/workflows/compat.yml`.
- A component change deploys only after a release and a full Home Assistant restart, not a reload.

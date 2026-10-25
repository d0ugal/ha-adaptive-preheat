# Development

## Setup and checks

Everything runs through [uv](https://docs.astral.sh/uv/) with a locked environment.

```bash
uv sync --locked --managed-python --python 3.13
make test        # pytest: pure model tests (no HA) and integration tests
make lint        # ruff check and format check, run in the pinned ruff container
make format      # ruff fix and format
make simulate    # synthetic rooms: learning curve (scripts/simulate.py)
uvx pyright==1.1.408   # same pyright version as CI; rtm_core is held to strict
```

`pytest` enforces **95% branch coverage** (`fail_under` in `pyproject.toml`). Every change
comes with a test: pure logic goes in `tests/model/` (it must import `rtm_core` only, never
Home Assistant), anything touching the coordinator or entities in `tests/integration/` using
the `FakeModel` double.

The model core, `custom_components/room_thermal_model/rtm_core/`, is standard-library Python
with no Home Assistant imports, behind the `ThermalModel` interface. New learning logic
belongs there so it stays testable alone. Run scoring (`rtm_core/scoring.py`) is an example.

## History import and the export script

`room_thermal_model.import_history` reads a JSON-lines file (format in the README) with
`rtm_core/history_import.py`, a pure parser that rejects the whole file on any bad line.
`scripts/export_prometheus_history.py` builds such a file from any Prometheus-compatible
`query_range` API (stdlib only, chunks below 11000 points, one series per expression). Its
pure parts and a run against a local HTTP server are covered by
`tests/model/test_export_prometheus_history.py`. The stored model grid is run-length encoded
(`rtm_core/grid.py`); the plain columns of earlier snapshots are still read.

## Zone groups

`rtm_core/shared_prior.py` (pure) builds a `GroupPrior` from `PeerSummary` values;
`zone_group.py` keeps the registry in `hass.data[DOMAIN]["zone_groups"]` (frozen summaries
only, no references to coordinators). The coordinator joins on setup, publishes after every
fit and leaves on unload; before a fit, under the model lock and in the event loop, it picks
the prior and hands the immutable copy to `model.set_prior`, so peers unloading while the
executor fits cannot change it. `ThermalModel.set_prior` is an optional hook (no-op by
default). The thresholds are `MIN_PEER_MATURITY` and `MAX_OWN_MATURITY` (both 0.3).

## Commits and pull requests

The repository is public: commit with the GitHub noreply identity, use generic names in code, tests, docs and PR text, and never include data from a real home. See the `Privacy` check below.

- Conventional commits (`feat:`, `fix:`, `build:`, `ci:`, `docs:`, `test:`, `chore:`);
  `!` and a `BREAKING CHANGE:` footer for breaking changes. A check enforces this on pull
  requests, and release-please reads them to pick the next version.
- Atomic commits, one logical change each. Open a pull request, do not push to `main`.

## CI

| Workflow | What it checks |
| --- | --- |
| `CI` | `ruff` lint and format, tests, and a 14 day simulation |
| `Coverage` | the suite with branch coverage; fails under 95% |
| `Pyright` | type check, strict on `rtm_core` |
| `Compatibility` | the suite on the oldest Home Assistant in `hacs.json` and on the newest stable; a daily canary runs against the newest plugin release, pre-releases allowed |
| `Validate` | hassfest and HACS validation |
| `Privacy` | `scripts/check_privacy.py`: no private addresses, home paths, credentials, personal commit emails or other personal data in the commits of a change (the public log never echoes matches) |
| `Workflow Lint` | `actionlint` and `zizmor` (action pinning, permissions) |
| `CodeQL`, `Dependency Review`, `Scorecard` | security and supply chain |
| `Release Please` | opens and maintains the release pull request |

Third party actions are pinned by commit SHA. Renovate keeps actions and dependencies
current (`renovate.json5`). The Home Assistant version under test is selected by the
`pytest-homeassistant-custom-component` version; the mapping and how to bump the minimum are
documented at the top of `.github/workflows/compat.yml`.

## Releasing

1. Merge pull requests with conventional commit titles to `main`.
2. release-please opens a "chore(main): release X.Y.Z" pull request: it bumps `pyproject.toml`,
   `manifest.json` and the changelog. Merging it tags the release and publishes it.
3. `uv.lock` is refreshed for you. release-please bumps the version in `pyproject.toml` but
   cannot update `uv.lock`, which would make `uv sync --locked` fail on `main` after every
   release. The `refresh-lock` job in `release-please.yml` runs `uv lock` on the release pull
   request's branch and commits the result. That push uses `GITHUB_TOKEN`, so the checks on
   the release pull request do not re-run; `main` is checked when it merges. If the job ever
   fails, `uv lock` and commit `uv.lock` by hand (`build: refresh uv.lock for vX.Y.Z`).

## Deploying to a Home Assistant host

The integration is a normal custom component. The reference deployment keeps this repository as
a git submodule, pinned to a release tag, and copies `custom_components/room_thermal_model/` into
the Home Assistant config on each update. Changes to a custom component need a full Home
Assistant restart, not a reload.

## Fork

This is a fork of [giogandola98/ha-room-thermal-model](https://github.com/giogandola98/ha-room-thermal-model).
The fork adds the CI and release tooling above, strict typing of the core, a coverage gate and run
scoring. The learning model itself is the upstream design (see `PLAN.md`).

## Evaluated and not adopted

- A stronger prior on the loss rate from the free-cooling time constant (`cooling.py`),
  applied when heating data is thin. On a year of real data in six rooms, a soft prior
  (worth up to 400 h of data) left the fit unchanged, because hundreds of hours of cooling
  data outvote it. A prior strong enough to matter is the same as fixing the loss rate, which
  gave plausible time constants (25 to 45 h) but a maximum reachable temperature that was
  still implausible whenever the heating rate was a starting value, and a worse holdout
  skill in several windows (up to 6 points worse than persistence-relative skill, and
  clearly worse in one room with almost no heating). Fit trust (`trust.py`) handles these
  fits instead.

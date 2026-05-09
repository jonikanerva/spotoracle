# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant **custom integration** distributed via HACS that produces a 0–96h electricity-price forecast for Finland (FI bidding zone) at native 15-minute resolution. It is read by Home Assistant as a single sensor (`sensor.spotoracle_forecast`) whose `forecast` attribute (384 entries spanning 4 full local days) drives an ApexCharts dashboard card.

## Architecture

Three thin layers, intentionally separated:

- **`predictor.py`** — pure logic, no HA imports. Inputs are plain dicts (Fingrid records, Nord Pool prices); output is a list of `{start, price, source}`. Easy to unit-test without mocking Home Assistant.
- **`coordinator.py`** — I/O boundary. `DataUpdateCoordinator` that polls Fingrid (one HTTP call per cycle, multi-dataset endpoint splits by `datasetId`), reads the user's price sensor from `hass.states`, and handles errors via `UpdateFailed`.
- **`sensor.py`** — thin entity layer. A single `CoordinatorEntity` exposing `native_value` (current quarter's price) and `extra_state_attributes` (the full 384-entry forecast plus diagnostics). No computation here.

The pipeline runs every 30 min in `coordinator.py`'s `_async_update_data`:

1. Read the user's existing Nord Pool sensor (`hass.states.get(price_sensor).attributes.prices`). Source of truth for actual day-ahead prices.
2. Fetch four Fingrid datasets in **one** HTTP call to `/api/data?datasets=245,75,165,124`.
3. Pass everything to `predictor.build_forecast`, which runs a closed-form 2-parameter OLS fit `price = a · residual + b` where `residual = consumption − wind`, all on 15-min quarter keys.
4. Output: 384 entries `{start: ISO8601, price: float, source: "nordpool"|"predicted"}` spanning local midnight today through 4 days ahead, with **no gaps and no null prices**.

## Repository layout

```
spotoracle/                            # GitHub repo root (HACS reads from here)
├── README.md                          # User-facing docs, also rendered in HACS
├── hacs.json                          # HACS metadata
├── custom_components/spotoracle/      # The integration HA loads at runtime
│   ├── manifest.json                  # domain, version (bump every release), iot_class
│   ├── const.py                       # Dataset IDs, defaults, all knobs in one place
│   ├── __init__.py                    # async_setup_entry, single coordinator instance
│   ├── config_flow.py                 # UI setup: API key + price-sensor EntitySelector
│   ├── coordinator.py                 # DataUpdateCoordinator → Fingrid `/data` endpoint
│   ├── predictor.py                   # Pure algorithm, NO HA imports, easy to unit-test
│   ├── sensor.py                      # Single CoordinatorEntity exposing `forecast`
│   └── strings.json + translations/   # Config-flow text (en + fi)
├── tests/                             # unittest-based regression tests for predictor.py
└── avoindata-api.yaml                 # Fingrid OpenAPI spec, gitignored, dev reference
```

## Datasets in use

| Fingrid ID | Resolution | Role |
|---|---|---|
| 245 | 15 min, ~72h ahead | Wind power generation forecast |
| 75  | 15 min            | Actual wind power, used to fill the 72–96h tail with the same weekday a week ago |
| 165 | 15 min, ~24h ahead | Consumption forecast (used while available) |
| 124 | hourly            | Actual past consumption — expanded 4× per hour by `expand_hourly_to_quarters`, used to fill in the 25–96h slot from the same weekday a week ago |

The user's price sensor is the **only** source of actual day-ahead prices; the integration never queries Nord Pool / ENTSO-E / elering directly. The price sensor format is documented in `README.md` under "Source price sensor requirements".

## Language Policy

- **All git content in English**: code, comments, commit messages, branch names, PR titles, README.md, CLAUDE.md, error and log messages.
- **Only exception**: `translations/fi.json` (Home Assistant UI localization for Finnish-speaking users — by design this file holds Finnish strings).
- Conversational chat in Claude Code sessions can be in any language the user prefers; that is separate from what gets committed.

## Code Standards (Python)

- **Type hints required** on all new function definitions and method signatures. Avoid `Any` as a bypass.
- **Pure functions preferred.** Side effects (HTTP, HA state reads/writes, logging) live at the I/O boundary in `coordinator.py` and `sensor.py`. Keep `predictor.py` import-free of `homeassistant`.
- **UTC everywhere internally.** All quarter keys are ISO8601 UTC strings floored to the 15-min boundary (`_quarter_key`). Timezone conversion happens only at the boundary (HA `dt_util.now()` → UTC for storage; UTC → local only for display, never internally).
- **Single-purpose functions** with descriptive English names.
- **No silent failures on user-visible state.** A missing price sensor or an HTTP error must surface as `UpdateFailed`, not a quietly-empty forecast.

## Dependencies

- **Never add a new dependency without research and an explicit reason recorded in the PR / commit message**: name, purpose, install size, maintenance status (last release, open issues), alternatives evaluated.
- **Don't add numpy/pandas/scikit-learn.** `fit_linear` is closed-form 2-parameter OLS in ~10 lines. HA Core does not allow heavy deps for community integrations, and the algorithm is intentionally simple and explainable.
- **Don't query Nord Pool / ENTSO-E / elering directly** — the user provides their own price sensor (with their own fees, transport tariffs, etc.). The integration is unit-and-fees agnostic by design.
- **Don't introduce a `weather.*` entity dependency** — Fingrid forecasts already incorporate weather. Discussed and explicitly deferred.

## Verification

Run before every commit:

```bash
# Python syntax check
python3 -m py_compile custom_components/spotoracle/*.py && echo OK

# Unit tests (predictor invariants)
python3 -m unittest discover -v tests

# JSON validation (manifest, hacs.json, strings, translations)
python3 -c "import json,sys; [json.load(open(p)) for p in sys.argv[1:]]" \
  hacs.json custom_components/spotoracle/manifest.json \
  custom_components/spotoracle/strings.json \
  custom_components/spotoracle/translations/*.json && echo OK
```

There are **no installed dependencies** — `aiohttp` and `voluptuous` come from Home Assistant at runtime; locally they are not needed because `predictor.py` (the only fast-iteration target) imports neither.

For ad-hoc smoke tests beyond what the unit tests cover, this template still works:

```python
python3 -c "
import sys; sys.path.insert(0, 'custom_components/spotoracle')
from predictor import build_forecast
# build mock nordpool_prices, wind_records, wind_actual_records,
# consumption_forecast_records, consumption_actual_records (hourly!),
# then call build_forecast(...) and assert len(result['series']) == 384.
"
```

## Git Workflow

- **Releases must go through a feature branch + GitHub pull request.** Any change that bumps `custom_components/spotoracle/manifest.json` `version` lands on a `release/vX.Y.Z` branch, is pushed to origin, and is merged via `gh pr create` → review → merge. Never bump `version` directly on `main`.
- Direct commits to `main` are fine for small non-version fixes (typos, comment cleanup, README clarifications, etc.).
- Use a feature branch + merge also when: a) the change spans multiple releases, or b) you have in-progress work that should not be visible on `main`.
- Tag (`vX.Y.Z`) only after the release PR has been merged into `main`.
- **Never force-push to `main`. Never commit secrets or credentials.**

## Release process

HACS shows commit hashes ("Installed version e04e229", "Latest version 04f7aa5") instead of versions when **GitHub Releases are missing**. A bare `git tag` + push is not enough — HACS requires GitHub Releases.

Order of operations every release:

1. Create a release branch: `git checkout -b release/vX.Y.Z`.
2. Bump `custom_components/spotoracle/manifest.json` `version` to the new value.
3. Update `README.md` if user-visible attributes / behaviour changed.
4. `git commit` (let GPG signing happen — this requires `dangerouslyDisableSandbox: true` because gpg-agent needs `~/.gnupg/`).
5. `git push -u origin release/vX.Y.Z`
6. Open the PR: `gh pr create --title "vX.Y.Z: ..." --body "..."`. Review, then merge.
7. After merge: `git checkout main && git pull --ff-only`.
8. `git tag vX.Y.Z && git push origin vX.Y.Z`.
9. **`gh release create vX.Y.Z --repo jonikanerva/spotoracle --title "..." --notes "..."`** ← this is the step that surfaces the version in HACS.

The user (`@jonikanerva`) is the codeowner; SSH for git and `gh` for releases are both authenticated as them on this machine.

Sandbox restrictions: GPG signing and SSH push need `dangerouslyDisableSandbox: true`. The user is aware of this; the `/sandbox` command is the proper long-term mitigation.

## Conventions worth knowing

- All keys in `predictor.py` dicts are **ISO8601 UTC strings** floored to the 15-min quarter boundary (`_quarter_key`). Mixing local and UTC will silently fail.
- The forecast output **inherits its unit** from the source price sensor's `unit_of_measurement` attribute (c/kWh, EUR/MWh — whatever the user has). Never hardcode a unit in `sensor.py`.
- `MIN_FIT_SAMPLES` is in **quarters**, not hours. 24 quarters = 6h overlap. If you change the resolution again, change this together.
- Diagnostic attributes on the sensor (`slope`, `intercept`, `fit_samples`, `fit_used_default`, `consumption_extended_quarters`, `wind_extended_quarters`, `generated_at`) are intentional debugging surface — keep them.
- `extend_with_last_week` is a **deliberate approximation**, not a hidden ML model. Finnish electricity consumption has a strong weekly cycle, so copying same-weekday-same-quarter from 7 days ago is good enough for the 25–96h tail. Document any future replacement (e.g. multi-week mean, seasonal model) as such.
- `merge_actual_and_predicted` is contractual: returns exactly `num_quarters` entries in chronological order, no gaps, no null prices. Forward-fill from the most recent predicted value when both `actual` and `predicted` miss a quarter.

## Out of scope (don't do these)

- Don't add a YAML configuration option — config flow only. The user does not edit `configuration.yaml`.
- Don't return shorter forecast series. The 384-entry contract above is load-bearing for the ApexCharts card layout.

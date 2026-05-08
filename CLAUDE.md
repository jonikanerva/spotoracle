# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant **custom integration** distributed via HACS that produces a 0–72h electricity-price forecast for Finland (FI bidding zone) at native 15-minute resolution. It is read by Home Assistant as a single sensor (`sensor.spotoracle_forecast`) whose `forecast` attribute (288 entries) drives an ApexCharts dashboard card.

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
└── avoindata-api.yaml                 # Fingrid OpenAPI spec, gitignored, dev reference
```

## Architecture: the data flow

The pipeline runs every 30 min in `coordinator.py`'s `_async_update_data`:

1. **Read** the user's existing Nord Pool sensor (`hass.states.get(price_sensor).attributes.prices`). This is the source of truth for actual day-ahead prices.
2. **Fetch** three Fingrid datasets in **one** HTTP call to `/api/data?datasets=245,165,124` — the multi-dataset endpoint splits by `datasetId` in the response.
3. Pass everything to `predictor.build_forecast`, which runs a closed-form 2-parameter OLS fit `price = a · residual + b` where `residual = consumption − wind`, all on 15-min quarter keys.
4. Output: 288 entries `{start: ISO8601, price: float, source: "day_ahead"|"predicted"}` for `now → now + 72h`.

**Key design choice — `predictor.py` has no HA dependencies.** It is pure Python with `datetime` and `Iterable` only; everything else (Fingrid records, Nord Pool prices) is passed in as plain dicts. This is what makes ad-hoc smoke-testing trivial.

**Key design choice — no numpy/pandas/ML.** `fit_linear` is closed-form 2-parameter OLS in ~10 lines. HA Core does not allow heavy deps for community integrations, and the algorithm is intentionally simple-and-explainable.

## Datasets in use

| Fingrid ID | Resolution | Role |
|---|---|---|
| 245 | 15 min, 72h ahead | Wind power generation forecast |
| 165 | 15 min, ~24h ahead | Consumption forecast (used while available) |
| 124 | hourly | Actual past consumption — expanded 4× per hour by `expand_hourly_to_quarters`, used to fill in 25–72h slot from the same weekday a week ago |

The user's price sensor is the **only** source of actual day-ahead prices; the integration never queries Nord Pool / ENTSO-E / elering directly. The price sensor format is documented in `README.md` under "Lähde-hintasensorin vaatimukset".

## Development commands

There is **no test framework, no build step, no CI**. Validation is ad-hoc:

```bash
# Python syntax check (after editing .py files):
python3 -m py_compile custom_components/spotoracle/*.py && echo OK

# JSON validation (manifest, hacs.json, strings, translations):
python3 -c "import json,sys; [json.load(open(p)) for p in sys.argv[1:]]" \
  hacs.json custom_components/spotoracle/manifest.json \
  custom_components/spotoracle/strings.json \
  custom_components/spotoracle/translations/*.json && echo OK

# Smoke-test predictor end-to-end with synthetic data — see prior commits
# for full mock setups; quick template:
python3 -c "
import sys; sys.path.insert(0, 'custom_components/spotoracle')
from predictor import build_forecast
# build mock nordpool_prices, wind_records, consumption_forecast_records,
# consumption_actual_records (hourly!), then call build_forecast(...)
# and assert len(result['series']) == 288.
"
```

There are **no installed dependencies** — `aiohttp` and `voluptuous` come from Home Assistant at runtime; locally they are not needed because `predictor.py` (the only fast-iteration target) imports neither.

## Release process

HACS shows commit hashes ("Installed version e04e229", "Latest version 04f7aa5") instead of versions when **GitHub Releases are missing**. A bare `git tag` + push is not enough — HACS requires GitHub Releases.

Order of operations every release:

1. Bump `custom_components/spotoracle/manifest.json` `version` to the new value.
2. Update `README.md` if user-visible attributes / behaviour changed.
3. `git commit` (let GPG signing happen — this requires `dangerouslyDisableSandbox: true` because gpg-agent needs `~/.gnupg/`).
4. `git push origin main`
5. `git tag vX.Y.Z && git push origin vX.Y.Z`
6. **`gh release create vX.Y.Z --repo jonikanerva/spotoracle --title "..." --notes "..."`** ← this is the step that surfaces the version in HACS.

The user (`@jonikanerva`) is the codeowner; SSH for git and `gh` for releases are both authenticated as them on this machine.

Sandbox restrictions: GPG signing and SSH push need `dangerouslyDisableSandbox: true`. The user is aware of this; the `/sandbox` command is the proper long-term mitigation.

## Conventions worth knowing

- All keys in `predictor.py` dicts are **ISO8601 UTC strings** floored to the 15-min quarter boundary (`_quarter_key`). Mixing local and UTC will silently fail.
- The forecast output **inherits its unit** from the source price sensor's `unit_of_measurement` attribute (snt/kWh, c/kWh, EUR/MWh — whatever the user has). Never hardcode a unit in `sensor.py`.
- `MIN_FIT_SAMPLES` is in **quarters**, not hours. 24 quarters = 6h overlap. If you change the resolution again, change this together.
- Diagnostic attributes on the sensor (`slope`, `intercept`, `fit_samples`, `fit_used_default`, `consumption_extended_quarters`) are intentional debugging surface — keep them.
- `extend_consumption_with_last_week` is a **deliberate approximation**, not a hidden ML model. Finnish electricity consumption has a strong weekly cycle, so copying same-weekday-same-quarter from 7 days ago is good enough for 25–72h. Document any future replacement (e.g. multi-week mean, seasonal model) as such.

## Out of scope (don't do these)

- Don't add numpy/pandas/scikit-learn — see "no heavy deps" above.
- Don't query Nord Pool / ENTSO-E / elering directly — the user provides their own price sensor (e.g. integrated fees, transport tariffs). The integration is unit-and-fees agnostic by design.
- Don't introduce a `weather.*` entity dependency — Fingrid forecasts already incorporate weather. The user's `weather.forecast_koti` was discussed and explicitly deferred.
- Don't add a YAML configuration option — config flow only. The user does not edit `configuration.yaml`.

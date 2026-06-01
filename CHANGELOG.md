# Changelog

All notable changes to SpotOracle are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.2.0] - 2026-06-01

### Changed
- **Multi-week wind tail extension.** Past Fingrid's ~72h wind forecast horizon,
  the day-2/3 tail now averages the same weekday/quarter over the **last 4 weeks**
  instead of copying a single week. Wind has no weekly cycle, so one week ago is
  essentially noise; averaging pulls the estimate toward the local climatology.
  Measured (Apr–May 2026): day-3 rank correlation (Spearman) 0.65 → 0.68,
  P@8cheapest 0.61 → 0.64; day-1/2 unaffected. Consumption keeps the single-week
  copy (its strong weekly cycle makes one week ago the best proxy). This
  improves day-3 **ranking** (the cheap/expensive ordering automations use); the
  absolute day-3 level shifts slightly, consistent with the ranking-first goal.
- `HISTORY_DAYS` raised from 8 to 29 so the 4-week wind look-back has data. This
  enlarges each Fingrid fetch but stays within one request (no pagination).
- New knob `WIND_EXTENSION_WEEKS` (const) / `build_forecast(wind_extension_weeks=…)`;
  `extend_with_last_week` gained a `weeks` parameter (default 1, backward-compatible).

## [2.1.1] - 2026-06-01

### Fixed
- **Dataset 124 (actual consumption) is now read at its native 15-minute
  resolution.** Fingrid moved this dataset to 15-min after the 2025 MTU shift,
  but the integration still treated it as hourly and collapsed each hour into a
  single value (the last 15-min sample of the hour won and was smeared across
  all four quarters). `expand_hourly_to_quarters` is now resolution-agnostic:
  genuine 15-min input passes through with its distinct quarter values intact,
  while truly hourly input is still filled from the `:00` value for backward
  compatibility. This only affects the post-Fingrid-horizon tail extension that
  124 feeds; measured forecast accuracy is essentially unchanged (day-1 rank
  0.77 → 0.78), but the consumption inputs are no longer distorted.

## [2.1.0] - 2026-05-31

### Added
- **Hour-of-day bias correction.** The forecast now layers an additive
  per-UTC-hour bias on top of the linear regression, learned from your source
  sensor's own published prices (the mean of `actual − predicted` grouped by
  hour). This recovers the daily price rhythm — morning/evening peaks, night
  troughs — that the `consumption − wind` residual model alone cannot express.
  Measured on April–May 2026 data, day-1 rank correlation (Spearman) improved
  0.38 → 0.77, day-2 0.30 → 0.70, day-3 0.22 → 0.64, and mean absolute error
  3.76 → 3.50 c/kWh; the forecast now beats the naive "same quarter yesterday /
  last week" baselines on ranking. Toggle via `build_forecast(apply_time_bias=…)`
  (defaults on).
- **Backtest harness** under `tools/` — developer tooling, not shipped to Home
  Assistant: rolling-origin evaluation with a look-ahead firewall, rank-first
  metrics (Spearman, precision@N for the cheapest/most-expensive hours) reported
  per forecast horizon, and naive baselines to beat. See `fetch_backtest_data.py`,
  `backtest.py`, `metrics.py`, `vintage.py`, and `tests/test_backtest.py`.
- `hour_bias_buckets` diagnostic in the `custom_components.spotoracle` debug log
  (the number of hour buckets the bias was learned over).

### Changed
- README "Fees and transmission tariffs": a time-of-day fee pattern is now
  largely captured by the hour-of-day bias correction rather than averaged away.

## [2.0.0] - 2026-05-31

### Changed
- **Breaking: the forecast no longer passes published prices through.** The
  `forecast` series now contains only the integration's own predictions and
  begins one 15-minute quarter after the last price your source sensor
  publishes — typically the start of tomorrow, or the day after once tomorrow's
  day-ahead prices arrive. Your published prices are still read, but only as the
  regression's fit target; they are never echoed back. Overlay the forecast
  against your own price sensor in ApexCharts for a continuous past→future view.
- **Breaking: fixed series length changed from 384 (4 days) to 288 (3 days).**
  The window is a constant 3 days forward (`FORECAST_DAYS`); only its start
  shifts with how much your sensor already covers. Forecast entries are now
  `{start, price}` — there is no per-entry `source` field.
- **Breaking: minimal sensor attributes.** The entity now exposes only
  `forecast`, `generated_at`, and a new `degraded` flag. The previous numeric
  diagnostics are still computed and written to the
  `custom_components.spotoracle` debug log, but are no longer entity attributes.

### Added
- `degraded` attribute: `true` when the forecast should not be trusted — the
  price model fell back to default coefficients (too little overlap with your
  source sensor) or some quarters had no Fingrid data and were zero-filled.

### Removed
- Sensor attributes `source`, `slope`, `intercept`, `fit_samples`,
  `fit_used_default`, `consumption_extended_quarters`, `wind_extended_quarters`,
  `filled_quarters`, `zero_seeded_quarters`, `prediction_floor`, and
  `prediction_floor_clipped_quarters`. The numeric values remain in the debug
  log.
- The per-entry `source` field on forecast entries (it previously
  distinguished `nordpool` from `predicted`); entries are now `{start, price}`.

## [1.0.0] - 2026-05-09

First stable release. The integration has been running in production
since v0.7.2 with no behaviour regressions; 1.0.0 marks the API and
contract as stable.

### Added
- `CHANGELOG.md` — historical and forward-looking release notes.
- GitHub Actions CI workflow: `py_compile`, pytest, JSON validation on
  every pull request and push to `main`. CI runs on Python 3.13 (the
  minimum Home Assistant Core 2026.x supports).
- Pytest-based test coverage for the I/O layer:
  `tests/test_coordinator.py` (14 tests covering Fingrid HTTP success
  and error paths, dataset splitting, floor-from-LTS percentile math,
  cache TTL, end-to-end setup) and `tests/test_config_flow.py` (8 tests
  covering the user flow, reconfigure flow, and validation rules).
  Test count: 19 → 41.
- `requirements_test.txt` pinning `pytest-homeassistant-custom-component`
  exactly. Test-only — runtime still declares zero Python dependencies.

### Changed
- `README.md` "Fees and transmission tariffs" no longer promises a future
  hour-of-day bias correction; the limitation is now documented as
  current behaviour. If you need tariff-aware pricing, encode the
  time-of-day rates into the source sensor itself (e.g. via a template
  sensor) so the forecast inherits them.
- `CLAUDE.md` release process now mandates a `CHANGELOG.md` rotation as
  part of every release branch.

## [0.7.2] - 2026

### Changed
- The current-price sensor used for floor calibration is now **required**.
  Existing installs from v0.7.0 or v0.7.1 surface as needing reconfiguration
  after the update; the reconfigure flow preserves the API key and price
  history.

## [0.7.1] - 2026

### Added
- Prediction floor: the integration derives a per-installation lower bound
  (5th percentile of the floor sensor's hourly minimums over the last
  30 days) from a user-supplied current-price sensor and clips predicted
  quarters that would otherwise extrapolate below it.
- `prediction_floor` and `prediction_floor_clipped_quarters` diagnostic
  attributes on the sensor.
- README guide for the prediction floor and `CLAUDE.md` conventions for
  keeping the predictor HA-import-free while accepting a floor parameter.

## [0.7.0] - 2026

### Added
- Outage diagnostics: `filled_quarters` and `zero_seeded_quarters` sensor
  attributes surface forward-fill and hard-fallback counts so users can see
  data thinning vs. real outages.
- `quarter_key` is now part of the predictor's public surface.

### Changed
- Hardened Fingrid response parsing against missing or malformed records.

## [0.6.0] - 2026

### Added
- Initial unit-test suite for `predictor.py` covering the regression fit,
  `expand_hourly_to_quarters`, `extend_with_last_week`, and
  `merge_actual_and_predicted` invariants.

### Changed
- Predictor robustness improvements (defensive parsing, clearer fallbacks).
- README and code comments translated to English; Finnish retained only in
  `translations/fi.json` (HA UI localization).

### Fixed
- Multiple smaller bugs in the forecast pipeline.

## [0.5.1] - 2026

### Added
- Brand icon in `icons/`.

### Changed
- Dropped the "(FI)" suffix from the integration name.

## [0.5.0] - 2026

### Added
- Reactive refresh: the integration listens for state changes on the source
  price sensor and refreshes immediately when its `prices` attribute
  changes.
- 4-day full-local-day forecast series (384 entries) starting at local
  midnight today.

### Changed
- Renamed the source-sensor concept to "Nord Pool sensor" throughout.

## [0.4.0] - 2026

### Changed
- Forecast series now starts at local midnight today (instead of the next
  available quarter), giving the ApexCharts card a clean day-aligned axis.
- Refined README ApexCharts example.

## [0.3.0] - 2026

### Changed
- Native 15-minute forecast resolution end-to-end (regression fit and
  output series both operate in 15-minute steps), aligned with Nord Pool's
  2025 move to 15-minute MTU pricing.

## [0.2.0] - 2025

### Added
- 72-hour forecast horizon.
- 15-minute spot price aggregation.

## [0.1.0] - 2025

### Added
- Initial release.

[Unreleased]: https://github.com/jonikanerva/spotoracle/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/jonikanerva/spotoracle/compare/v2.1.1...v2.2.0
[2.1.1]: https://github.com/jonikanerva/spotoracle/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/jonikanerva/spotoracle/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/jonikanerva/spotoracle/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/jonikanerva/spotoracle/compare/v0.7.2...v1.0.0
[0.7.2]: https://github.com/jonikanerva/spotoracle/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/jonikanerva/spotoracle/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/jonikanerva/spotoracle/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/jonikanerva/spotoracle/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/jonikanerva/spotoracle/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/jonikanerva/spotoracle/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/jonikanerva/spotoracle/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jonikanerva/spotoracle/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jonikanerva/spotoracle/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jonikanerva/spotoracle/releases/tag/v0.1.0

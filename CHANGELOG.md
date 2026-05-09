# Changelog

All notable changes to SpotOracle are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/jonikanerva/spotoracle/compare/v1.0.0...HEAD
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

"""Regression tests for the pure predictor module.

Run with:
    python3 -m unittest discover -v tests
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "spotoracle"))

from predictor import (  # noqa: E402  (sys.path tweak above)
    bucket_records,
    build_forecast,
    expand_hourly_to_quarters,
    parse_price_sensor_attributes,
    quarter_key,
)


def _make_quarter_records(start: datetime, count: int, value: float) -> list[dict]:
    return [
        {
            "startTime": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
            "value": value,
        }
        for i in range(count)
    ]


def _make_hourly_records(start: datetime, count: int, value: float) -> list[dict]:
    return [
        {
            "startTime": (start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "value": value,
        }
        for i in range(count)
    ]


def _make_price_entries(start: datetime, count: int, value: float) -> list[dict]:
    return [
        {
            "start": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
            "price": value,
        }
        for i in range(count)
    ]


class TestQuarterKey(unittest.TestCase):
    def test_floors_to_15min_boundary(self) -> None:
        cases = [
            (datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc), "2026-05-08T12:00:00+00:00"),
            (datetime(2026, 5, 8, 12, 7, tzinfo=timezone.utc), "2026-05-08T12:00:00+00:00"),
            (datetime(2026, 5, 8, 12, 14, tzinfo=timezone.utc), "2026-05-08T12:00:00+00:00"),
            (datetime(2026, 5, 8, 12, 15, tzinfo=timezone.utc), "2026-05-08T12:15:00+00:00"),
            (datetime(2026, 5, 8, 12, 23, tzinfo=timezone.utc), "2026-05-08T12:15:00+00:00"),
            (datetime(2026, 5, 8, 12, 59, tzinfo=timezone.utc), "2026-05-08T12:45:00+00:00"),
        ]
        for dt, expected in cases:
            with self.subTest(dt=dt):
                self.assertEqual(quarter_key(dt), expected)


class TestPriceSensorParsing(unittest.TestCase):
    def test_zero_price_is_preserved(self) -> None:
        prices = [{"start": "2026-05-08T00:00:00+00:00", "price": 0.0}]
        result = parse_price_sensor_attributes(prices)
        self.assertEqual(len(result), 1)
        self.assertEqual(list(result.values())[0], 0.0)

    def test_negative_price_is_preserved(self) -> None:
        prices = [{"start": "2026-05-08T00:00:00+00:00", "price": -1.5}]
        result = parse_price_sensor_attributes(prices)
        self.assertEqual(list(result.values())[0], -1.5)


class TestMalformedRecords(unittest.TestCase):
    def test_bad_timestamp_is_skipped_not_raised(self) -> None:
        records = [
            {"startTime": "BROKEN", "value": 50},
            {"startTime": "2026-05-08T00:00:00Z", "value": 100},
        ]
        result = bucket_records(records)
        self.assertEqual(len(result), 1)

    def test_non_numeric_value_is_skipped(self) -> None:
        records = [
            {"startTime": "2026-05-08T00:00:00Z", "value": "NOT_A_NUMBER"},
            {"startTime": "2026-05-08T00:15:00Z", "value": 42},
        ]
        result = bucket_records(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(list(result.values())[0], 42.0)


class TestNonDictRecords(unittest.TestCase):
    def test_string_in_price_list_is_skipped(self) -> None:
        prices = ["bad", {"start": "2026-05-08T00:00:00+00:00", "price": 4.21}]
        result = parse_price_sensor_attributes(prices)
        self.assertEqual(len(result), 1)
        self.assertEqual(list(result.values())[0], 4.21)

    def test_string_in_records_is_skipped(self) -> None:
        records = ["bad", {"startTime": "2026-05-08T00:00:00Z", "value": 100}]
        result = bucket_records(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(list(result.values())[0], 100.0)

    def test_string_in_hourly_records_is_skipped(self) -> None:
        records = ["bad", {"startTime": "2026-05-08T00:00:00Z", "value": 50}]
        result = expand_hourly_to_quarters(records)
        # Hourly expansion creates 4 quarters per valid record.
        self.assertEqual(len(result), 4)

    def test_only_invalid_items_yields_empty(self) -> None:
        garbage = ["a", 1, None, [], 3.14]
        self.assertEqual(parse_price_sensor_attributes(garbage), {})
        self.assertEqual(bucket_records(garbage), {})
        self.assertEqual(expand_hourly_to_quarters(garbage), {})


class TestBuildForecastInvariants(unittest.TestCase):
    def setUp(self) -> None:
        self.series_start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
        self.series_end = self.series_start + timedelta(days=4)
        self.history_start = self.series_start - timedelta(days=8)
        # 4 days × 96 quarters = 384 — full coverage of the series window.
        self.consumption_forecast = _make_quarter_records(
            self.series_start, 4 * 96, value=10000.0
        )
        # Wind forecast covers the same window.
        self.wind_forecast = _make_quarter_records(self.series_start, 4 * 96, value=2000.0)
        # Last-week actuals span both history and the projected series so
        # extend_with_last_week can fill any gaps if we trim forecasts.
        actual_span_quarters = (8 + 4) * 96
        self.consumption_actual_hourly = _make_hourly_records(
            self.history_start, (8 + 4) * 24, value=10000.0
        )
        self.wind_actual = _make_quarter_records(
            self.history_start, actual_span_quarters, value=2000.0
        )
        # Day-ahead price: cover only the first 24h to mirror reality where
        # day-ahead is published a day at a time.
        self.nordpool_prices = _make_price_entries(self.series_start, 96, value=4.0)

    def _build(self) -> dict:
        return build_forecast(
            nordpool_prices=self.nordpool_prices,
            wind_records=self.wind_forecast,
            wind_actual_records=self.wind_actual,
            consumption_forecast_records=self.consumption_forecast,
            consumption_actual_records=self.consumption_actual_hourly,
            series_start=self.series_start,
            series_end=self.series_end,
            default_slope=0.002,
            default_intercept=-2.0,
            min_fit_samples=24,
        )

    def test_series_length_is_384(self) -> None:
        result = self._build()
        self.assertEqual(len(result["series"]), 384)

    def test_no_gaps_in_series(self) -> None:
        result = self._build()
        series = result["series"]
        for i, point in enumerate(series):
            expected_ts = self.series_start + timedelta(minutes=15 * i)
            self.assertEqual(point["start"], expected_ts.isoformat())
            self.assertIsNotNone(point["price"])

    def test_source_values_are_valid(self) -> None:
        result = self._build()
        for point in result["series"]:
            self.assertIn(point["source"], {"nordpool", "predicted"})

    def test_default_fallback_when_too_few_samples(self) -> None:
        # Force fewer overlap samples than min_fit_samples by giving prices
        # for only a single quarter.
        self.nordpool_prices = _make_price_entries(self.series_start, 1, value=4.0)
        result = self._build()
        self.assertTrue(result["fit_used_default"])
        self.assertAlmostEqual(result["slope"], 0.002)
        self.assertAlmostEqual(result["intercept"], -2.0)

    def test_full_coverage_yields_zero_fill_stats(self) -> None:
        result = self._build()
        self.assertEqual(result["filled_quarters"], 0)
        self.assertEqual(result["zero_seeded_quarters"], 0)


class TestFillStatsEmptyInput(unittest.TestCase):
    """Hard outage: nothing from Fingrid, nothing from the price sensor."""

    def setUp(self) -> None:
        self.series_start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
        self.series_end = self.series_start + timedelta(days=4)

    def test_completely_empty_input_yields_zero_seeded_full_series(self) -> None:
        result = build_forecast(
            nordpool_prices=[],
            wind_records=[],
            wind_actual_records=[],
            consumption_forecast_records=[],
            consumption_actual_records=[],
            series_start=self.series_start,
            series_end=self.series_end,
            default_slope=0.002,
            default_intercept=-2.0,
            min_fit_samples=24,
        )
        self.assertEqual(len(result["series"]), 384)
        self.assertEqual(result["zero_seeded_quarters"], 384)
        self.assertEqual(result["filled_quarters"], 0)
        for point in result["series"]:
            self.assertEqual(point["price"], 0.0)
            self.assertEqual(point["source"], "predicted")


if __name__ == "__main__":
    unittest.main()

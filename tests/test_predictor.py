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
    last_priced_quarter,
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


class TestExpandToQuarters(unittest.TestCase):
    def test_hourly_input_fills_four_quarters(self) -> None:
        records = [{"startTime": "2026-05-08T00:00:00Z", "value": 100.0}]
        result = expand_hourly_to_quarters(records)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(v == 100.0 for v in result.values()))

    def test_15min_input_passes_through(self) -> None:
        # Regression: Fingrid dataset 124 is now 15-min. Distinct quarter values
        # must survive rather than being smeared to one value per hour (the old
        # expand-from-:00 behaviour collapsed them and the last write won).
        records = [
            {"startTime": "2026-05-08T00:00:00Z", "value": 10.0},
            {"startTime": "2026-05-08T00:15:00Z", "value": 20.0},
            {"startTime": "2026-05-08T00:30:00Z", "value": 30.0},
            {"startTime": "2026-05-08T00:45:00Z", "value": 40.0},
        ]
        result = expand_hourly_to_quarters(records)
        self.assertEqual(len(result), 4)
        self.assertEqual(sorted(result.values()), [10.0, 20.0, 30.0, 40.0])
        k15 = quarter_key(datetime(2026, 5, 8, 0, 15, tzinfo=timezone.utc))
        self.assertEqual(result[k15], 20.0)


class TestLastPricedQuarter(unittest.TestCase):
    def test_returns_none_for_empty_or_garbage(self) -> None:
        self.assertIsNone(last_priced_quarter([]))
        self.assertIsNone(last_priced_quarter(["garbage", 1, None]))

    def test_returns_latest_quarter_utc(self) -> None:
        start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
        prices = _make_price_entries(start, 96, value=4.0)  # 00:00 .. 23:45
        self.assertEqual(
            last_priced_quarter(prices),
            datetime(2026, 5, 8, 23, 45, tzinfo=timezone.utc),
        )

    def test_normalises_local_time_to_utc(self) -> None:
        # A +03:00 local entry must come back as its UTC instant so the series
        # starts one UTC quarter later.
        prices = [{"start": "2026-05-09T00:15:00+03:00", "price": 4.0}]
        self.assertEqual(
            last_priced_quarter(prices),
            datetime(2026, 5, 8, 21, 15, tzinfo=timezone.utc),
        )


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

    def test_series_covers_full_window(self) -> None:
        # build_forecast emits one predicted entry per quarter of the
        # [series_start, series_end) window it is given. Here that is 4 days =
        # 384 quarters. (The coordinator narrows this to a fixed 3-day window
        # starting after the last published price; that is tested separately.)
        result = self._build()
        self.assertEqual(len(result["series"]), 384)

    def test_no_gaps_in_series(self) -> None:
        result = self._build()
        series = result["series"]
        for i, point in enumerate(series):
            expected_ts = self.series_start + timedelta(minutes=15 * i)
            self.assertEqual(point["start"], expected_ts.isoformat())
            self.assertIsNotNone(point["price"])

    def test_entries_have_only_start_and_price(self) -> None:
        # The series is predicted-only, so entries carry no source field:
        # exactly {start, price}.
        result = self._build()
        for point in result["series"]:
            self.assertEqual(set(point), {"start", "price"})

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
            self.assertNotIn("source", point)


class TestPredictionFloor(unittest.TestCase):
    """Floor clipping prevents OLS extrapolation past the observed price range.

    Setup: 4-day series with a varied consumption forecast that produces
    distinct predicted residual buckets, pairing with Nord Pool prices that
    cover only day 0. The 96 overlap quarters all carry price=4.0 and
    residual=8000, which has zero variance → fit falls back to
    `fit_used_default=True` with default slope=0.002, intercept=-2.0.

    With those defaults, predictions per residual (the whole series is
    predicted-only — published prices are the fit target, not an output
    source):
      day 0 — residual 8000 → predicted 14.0 (above any floor in tests)
      day 1 — residual 8000 → predicted 14.0 (above any floor in tests)
      day 2 — residual 3000 → predicted 4.0  (below floor 5.0)
      day 3 — residual 1000 → predicted 0.0  (below floor 5.0)
    """

    def setUp(self) -> None:
        self.series_start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
        self.series_end = self.series_start + timedelta(days=4)
        self.history_start = self.series_start - timedelta(days=8)

        cons_d0 = _make_quarter_records(self.series_start, 96, value=10000.0)
        cons_d1 = _make_quarter_records(
            self.series_start + timedelta(days=1), 96, value=10000.0
        )
        cons_d2 = _make_quarter_records(
            self.series_start + timedelta(days=2), 96, value=5000.0
        )
        cons_d3 = _make_quarter_records(
            self.series_start + timedelta(days=3), 96, value=3000.0
        )
        self.consumption_forecast = cons_d0 + cons_d1 + cons_d2 + cons_d3
        self.wind_forecast = _make_quarter_records(
            self.series_start, 4 * 96, value=2000.0
        )

        actual_span_quarters = (8 + 4) * 96
        self.consumption_actual_hourly = _make_hourly_records(
            self.history_start, (8 + 4) * 24, value=10000.0
        )
        self.wind_actual = _make_quarter_records(
            self.history_start, actual_span_quarters, value=2000.0
        )
        self.nordpool_prices = _make_price_entries(self.series_start, 96, value=4.0)

    def _build(self, floor: float | None = None) -> dict:
        # These tests exercise the raw linear + floor behaviour with fixed
        # predicted values, so the additive hour-of-day bias is disabled (it is
        # covered separately in TestHourBias).
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
            floor=floor,
            apply_time_bias=False,
        )

    def test_floor_none_leaves_predictions_unchanged(self) -> None:
        result = self._build(floor=None)
        self.assertIsNone(result["prediction_floor"])
        self.assertEqual(result["prediction_floor_clipped_quarters"], 0)
        # Day 3 predictions reach 0.0 unmodified.
        day3_predicted = [
            p for p in result["series"] if p["start"].startswith("2026-05-11")
        ]
        self.assertTrue(day3_predicted, "expected day 3 predictions in series")
        self.assertTrue(
            all(p["price"] == 0.0 for p in day3_predicted),
            "day 3 predictions should be 0.0 without a floor",
        )

    def test_floor_clips_low_predictions_only(self) -> None:
        result = self._build(floor=5.0)
        self.assertEqual(result["prediction_floor"], 5.0)
        predicted = result["series"]
        for point in predicted:
            self.assertGreaterEqual(
                point["price"],
                5.0,
                f"Predicted point {point} dropped below floor 5.0",
            )
        # Day 1 predicted (residual 8000) keeps its 14.0 — above the floor.
        day1_predicted = [
            p for p in predicted if p["start"].startswith("2026-05-09")
        ]
        self.assertTrue(day1_predicted)
        self.assertTrue(all(p["price"] == 14.0 for p in day1_predicted))

    def test_prediction_floor_clipped_quarters_count(self) -> None:
        result = self._build(floor=5.0)
        # predict_series output covers all 4 days (residual exists for each).
        # Days 0 and 1 predict 14.0 (above floor); days 2 and 3 fall below
        # floor 5.0 → 96 + 96 = 192 clipped quarters.
        self.assertEqual(result["prediction_floor_clipped_quarters"], 192)

    def test_floor_works_with_default_fit_fallback(self) -> None:
        result = self._build(floor=5.0)
        self.assertTrue(
            result["fit_used_default"],
            "test setup is supposed to force fit_used_default=True",
        )
        self.assertGreater(result["prediction_floor_clipped_quarters"], 0)


class TestHourBias(unittest.TestCase):
    """The additive hour-of-day bias recovers a daily price rhythm that the
    linear residual model cannot express.

    Setup: flat residual (consumption 10000, wind 2000 -> residual 8000
    everywhere) so the linear fit has zero residual variance and falls back to
    the default coefficients, predicting a constant 14.0 for every quarter.
    The published day-0 prices instead alternate by UTC hour: 5.0 on even hours,
    15.0 on odd hours. With the bias on, the forecast must reproduce that
    alternation; with it off, every quarter stays at the flat 14.0.
    """

    def setUp(self) -> None:
        self.series_start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
        self.series_end = self.series_start + timedelta(days=4)
        history_start = self.series_start - timedelta(days=8)

        self.consumption_forecast = _make_quarter_records(
            self.series_start, 4 * 96, value=10000.0
        )
        self.wind_forecast = _make_quarter_records(self.series_start, 4 * 96, value=2000.0)
        self.consumption_actual_hourly = _make_hourly_records(
            history_start, (8 + 4) * 24, value=10000.0
        )
        self.wind_actual = _make_quarter_records(
            history_start, (8 + 4) * 96, value=2000.0
        )
        # Day-0 prices alternate by UTC hour: 5.0 (even), 15.0 (odd).
        self.nordpool_prices = [
            {
                "start": (self.series_start + timedelta(minutes=15 * i))
                .isoformat()
                .replace("+00:00", "Z"),
                "price": 5.0 if ((i // 4) % 2 == 0) else 15.0,
            }
            for i in range(96)
        ]

    def _build(self, apply_time_bias: bool) -> dict:
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
            apply_time_bias=apply_time_bias,
        )

    def _day1_by_hour(self, result: dict) -> dict[int, float]:
        out: dict[int, float] = {}
        for p in result["series"]:
            if p["start"].startswith("2026-05-09"):
                hour = int(p["start"][11:13])
                out[hour] = p["price"]
        return out

    def test_without_bias_forecast_is_flat(self) -> None:
        result = self._build(apply_time_bias=False)
        self.assertEqual(result["hour_bias_buckets"], 0)
        day1 = self._day1_by_hour(result)
        self.assertTrue(all(abs(v - 14.0) < 1e-9 for v in day1.values()))

    def test_bias_reproduces_hourly_profile(self) -> None:
        result = self._build(apply_time_bias=True)
        self.assertEqual(result["hour_bias_buckets"], 24)
        day1 = self._day1_by_hour(result)
        for hour, price in day1.items():
            expected = 5.0 if hour % 2 == 0 else 15.0
            self.assertAlmostEqual(price, expected, places=2, msg=f"hour {hour}")


if __name__ == "__main__":
    unittest.main()

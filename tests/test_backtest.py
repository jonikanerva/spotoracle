"""Tests for the backtest harness: metric math and the look-ahead firewall.

These run green with NO real data present — they validate the metric functions
on synthetic inputs with known answers, and the vintage-censoring rules that
prevent the backtest from leaking future data into a past forecast origin.

Run with:
    python3 -m unittest discover -v tests
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import backtest  # noqa: E402
import metrics  # noqa: E402
import vintage  # noqa: E402


class TestAbsoluteMetrics(unittest.TestCase):
    def test_mae_bias_known(self) -> None:
        self.assertAlmostEqual(metrics.mae([2, 4], [1, 2]), 1.5)
        self.assertAlmostEqual(metrics.bias([2, 4], [1, 2]), 1.5)

    def test_rmse_known(self) -> None:
        # errors [1, 2] -> sqrt((1 + 4) / 2) = sqrt(2.5)
        self.assertAlmostEqual(metrics.rmse([2, 4], [1, 2]), 2.5 ** 0.5)

    def test_bias_sign(self) -> None:
        # under-prediction -> negative bias
        self.assertAlmostEqual(metrics.bias([0, 0], [1, 3]), -2.0)

    def test_length_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            metrics.mae([1, 2], [1])


class TestSpearman(unittest.TestCase):
    def test_perfect_monotonic(self) -> None:
        self.assertAlmostEqual(metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_reversed(self) -> None:
        self.assertAlmostEqual(metrics.spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_constant_series_is_none(self) -> None:
        self.assertIsNone(metrics.spearman([5, 5, 5, 5], [1, 2, 3, 4]))

    def test_average_ranks_handles_ties(self) -> None:
        self.assertEqual(metrics._average_ranks([10, 20, 20, 40]), [1.0, 2.5, 2.5, 4.0])

    def test_tied_series_perfectly_correlated(self) -> None:
        self.assertAlmostEqual(metrics.spearman([1, 2, 2, 3], [5, 6, 6, 9]), 1.0)


class TestPrecisionAtN(unittest.TestCase):
    def test_cheap_perfect(self) -> None:
        self.assertEqual(metrics.precision_at_n([1, 2, 3, 4], [1, 2, 3, 4], 2, "cheap"), 1.0)

    def test_peak_perfect(self) -> None:
        self.assertEqual(metrics.precision_at_n([1, 2, 3, 4], [1, 2, 3, 4], 2, "peak"), 1.0)

    def test_disjoint_is_zero(self) -> None:
        # reversed actual -> cheapest predicted are actually the most expensive
        self.assertEqual(metrics.precision_at_n([1, 2, 3, 4], [4, 3, 2, 1], 2, "cheap"), 0.0)

    def test_n_clamped_to_length(self) -> None:
        self.assertEqual(metrics.precision_at_n([1, 2], [2, 1], 5, "cheap"), 1.0)

    def test_constant_prediction_is_none(self) -> None:
        # A flat forecast has no meaningful ranking -> None, not a misleading ~0.6.
        self.assertIsNone(metrics.precision_at_n([5, 5, 5, 5], [1, 2, 3, 4], 2, "cheap"))
        self.assertIsNone(metrics.classification_hit_rate([5, 5, 5, 5], [1, 2, 3, 4]))


class TestClassificationHitRate(unittest.TestCase):
    def test_perfect(self) -> None:
        vals = list(range(1, 13))
        self.assertEqual(metrics.classification_hit_rate(vals, vals), 1.0)

    def test_inverted_is_zero(self) -> None:
        vals = list(range(1, 13))
        self.assertEqual(metrics.classification_hit_rate(vals, vals[::-1]), 0.0)


class TestVintageCensorPrices(unittest.TestCase):
    def setUp(self) -> None:
        # Two local days of prices, Europe/Helsinki (+03:00 in May).
        self.prices = [
            {"start": "2026-05-10T00:00:00+03:00", "price": 1.0},
            {"start": "2026-05-10T23:45:00+03:00", "price": 2.0},
            {"start": "2026-05-11T00:00:00+03:00", "price": 3.0},
            {"start": "2026-05-11T23:45:00+03:00", "price": 4.0},
        ]

    def test_before_publication_excludes_tomorrow(self) -> None:
        T = datetime(2026, 5, 10, 13, 0, tzinfo=vintage.HELSINKI).astimezone(timezone.utc)
        kept = vintage.censor_prices(self.prices, T, publication_hour=14)
        prices = {p["price"] for p in kept}
        self.assertEqual(prices, {1.0, 2.0})  # only May 10

    def test_after_publication_includes_tomorrow(self) -> None:
        T = datetime(2026, 5, 10, 15, 0, tzinfo=vintage.HELSINKI).astimezone(timezone.utc)
        kept = vintage.censor_prices(self.prices, T, publication_hour=14)
        prices = {p["price"] for p in kept}
        self.assertEqual(prices, {1.0, 2.0, 3.0, 4.0})  # May 10 + May 11


class TestVintageCensorFingrid(unittest.TestCase):
    def test_forecast_censored_to_horizon(self) -> None:
        T = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
        recs = [
            {"startTime": (T - timedelta(hours=5)).isoformat(), "value": 1},  # history
            {"startTime": (T + timedelta(hours=10)).isoformat(), "value": 2},  # in horizon
            {"startTime": (T + timedelta(hours=30)).isoformat(), "value": 3},  # past horizon
        ]
        kept = vintage.censor_fingrid_forecast(recs, T, 24)
        self.assertEqual({r["value"] for r in kept}, {1, 2})

    def test_actuals_strictly_before_T(self) -> None:
        T = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
        recs = [
            {"startTime": (T - timedelta(hours=1)).isoformat(), "value": 1},
            {"startTime": (T + timedelta(hours=1)).isoformat(), "value": 2},
        ]
        kept = vintage.censor_actuals(recs, T)
        self.assertEqual({r["value"] for r in kept}, {1})


class TestForecastWindow(unittest.TestCase):
    def test_starts_one_quarter_after_last_price(self) -> None:
        prices = [{"start": "2026-05-10T21:45:00+00:00", "price": 1.0}]
        T = datetime(2026, 5, 10, 22, 0, tzinfo=timezone.utc)
        start, end = vintage.forecast_window(prices, T, forecast_days=3)
        self.assertEqual(start, datetime(2026, 5, 10, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(end, start + timedelta(days=3))

    def test_no_prices_falls_back_to_local_midnight(self) -> None:
        T = datetime(2026, 5, 10, 13, 0, tzinfo=vintage.HELSINKI).astimezone(timezone.utc)
        start, _ = vintage.forecast_window([], T, forecast_days=3)
        # local midnight of May 10 EET = 2026-05-09T21:00Z
        self.assertEqual(start, datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc))


class TestBacktestHelpers(unittest.TestCase):
    def test_horizon_buckets(self) -> None:
        s = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(backtest.horizon_of(s + timedelta(hours=1), s), "day1")
        self.assertEqual(backtest.horizon_of(s + timedelta(hours=25), s), "day2")
        self.assertEqual(backtest.horizon_of(s + timedelta(hours=49), s), "day3")
        self.assertIsNone(backtest.horizon_of(s + timedelta(hours=73), s))

    def test_to_hourly_averages_quarters(self) -> None:
        base = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        triples = [(base + timedelta(minutes=15 * i), float(i), float(i + 1)) for i in range(4)]
        preds, acts = backtest.to_hourly(triples, vintage.HELSINKI)
        self.assertEqual(len(preds), 1)  # all four quarters in one local hour
        self.assertAlmostEqual(preds[0], 1.5)  # mean(0,1,2,3)
        self.assertAlmostEqual(acts[0], 2.5)

    def test_baseline_last_week_uses_seven_days_back(self) -> None:
        T = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        target = "2026-05-11T00:00:00+00:00"
        src = backtest.quarter_key(vintage._parse(target) - timedelta(days=7))
        realized = {src: 9.0}
        ser = backtest.baseline_series("last_week", [target], realized, [], T)
        self.assertEqual(ser, [{"start": target, "price": 9.0}])

    def test_baseline_yesterday_skips_future_source(self) -> None:
        # day-2 target's "yesterday" source is still after T -> omitted.
        T = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        target = "2026-05-12T00:00:00+00:00"  # source 2026-05-11 >= T
        ser = backtest.baseline_series("yesterday", [target], {}, [], T)
        self.assertEqual(ser, [])

    def test_price_records_filters_missing_field(self) -> None:
        raw = [
            {"start": "2026-05-10T00:00:00+00:00", "totalCentsKwh": 8.8, "spotCentsKwh": 2.3},
            {"start": "2026-05-10T00:15:00+00:00", "totalCentsKwh": None, "spotCentsKwh": 1.0},
        ]
        total = backtest.price_records(raw, "totalCentsKwh")
        self.assertEqual(len(total), 1)
        self.assertEqual(total[0]["price"], 8.8)


if __name__ == "__main__":
    unittest.main()

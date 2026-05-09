"""Pure prediction logic. Quarter keys are ISO8601 UTC strings (15-min floor)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

_LOGGER = logging.getLogger(__name__)


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _quarter_floor(dt: datetime) -> datetime:
    """Floor a datetime down to the nearest 15-min quarter."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def _quarter_key(dt: datetime) -> str:
    return _quarter_floor(dt).isoformat()


# Public alias: external callers (e.g. sensor.py picking the current point)
# should not depend on the underscored internal name.
quarter_key = _quarter_key


def bucket_records(records: Iterable[dict]) -> dict[str, float]:
    """Bucket records by 15-min quarter. If multiple samples land in the same
    quarter (e.g. accidental higher-resolution input), take the mean.
    Malformed records are skipped with a debug log; one bad row never
    crashes the whole update.
    """
    buckets: dict[str, list[float]] = {}
    for r in records:
        if not isinstance(r, dict):
            _LOGGER.debug("Skipping non-dict record: %s", r)
            continue
        start = r.get("startTime") or r.get("start_time") or r.get("start")
        val = r.get("value")
        if start is None or val is None:
            continue
        try:
            key = _quarter_key(_parse_iso(start))
            quarter_value = float(val)
        except (ValueError, TypeError):
            _LOGGER.debug("Skipping malformed record: %s", r)
            continue
        buckets.setdefault(key, []).append(quarter_value)
    return {k: sum(vs) / len(vs) for k, vs in buckets.items()}


def parse_price_sensor_attributes(prices: Iterable[dict]) -> dict[str, float]:
    """Parse Nord Pool style price entries to a quarter-keyed dict.

    Nord Pool moved to 15-minute MTU pricing in 2025; the source sensor's
    `prices` attribute is expected to expose 15-min entries. If multiple
    samples per quarter exist, take the mean.
    """
    buckets: dict[str, list[float]] = {}
    for p in prices:
        if not isinstance(p, dict):
            _LOGGER.debug("Skipping non-dict price record: %s", p)
            continue
        start = p.get("start") or p.get("startTime")
        if "price" in p:
            price = p["price"]
        elif "value" in p:
            price = p["value"]
        else:
            continue
        if start is None or price is None:
            continue
        try:
            key = _quarter_key(_parse_iso(start))
            quarter_price = float(price)
        except (ValueError, TypeError):
            _LOGGER.debug("Skipping malformed price record: %s", p)
            continue
        buckets.setdefault(key, []).append(quarter_price)
    return {k: sum(vs) / len(vs) for k, vs in buckets.items()}


def expand_hourly_to_quarters(hourly_records: Iterable[dict]) -> dict[str, float]:
    """Expand hourly records into 4 quarter-keys per hour with the same value.

    Used for Fingrid datasets that are hourly resolution (e.g. 124, actual
    consumption) when the rest of the pipeline operates on 15-min quarters.
    """
    out: dict[str, float] = {}
    for r in hourly_records:
        if not isinstance(r, dict):
            _LOGGER.debug("Skipping non-dict hourly record: %s", r)
            continue
        start = r.get("startTime") or r.get("start_time") or r.get("start")
        val = r.get("value")
        if start is None or val is None:
            continue
        try:
            hour_dt = _parse_iso(start).replace(minute=0, second=0, microsecond=0)
            quarter_value = float(val)
        except (ValueError, TypeError):
            _LOGGER.debug("Skipping malformed hourly record: %s", r)
            continue
        for q in range(4):
            qts = hour_dt + timedelta(minutes=15 * q)
            out[_quarter_key(qts)] = quarter_value
    return out


def extend_with_last_week(
    forecast: dict[str, float],
    actual: dict[str, float],
    horizon_end: datetime,
) -> dict[str, float]:
    """Fill missing quarters after `forecast` ends with values from the same
    weekday/quarter one week ago, taken from `actual`. Returns a new dict.

    Used for both consumption (Finnish weekly demand pattern is strong) and
    wind power (rougher proxy, but acceptable for the last 6–24h tail).
    """
    out = dict(forecast)
    if not forecast:
        return out
    last_known = _parse_iso(max(forecast))
    cursor = last_known + timedelta(minutes=15)
    while cursor < horizon_end:
        prev_week = cursor - timedelta(days=7)
        prev_key = _quarter_key(prev_week)
        if prev_key in actual:
            out[_quarter_key(cursor)] = actual[prev_key]
        cursor += timedelta(minutes=15)
    return out


def align_series(
    price_dict: dict[str, float], residual_dict: dict[str, float]
) -> tuple[list[float], list[float]]:
    common = sorted(set(price_dict) & set(residual_dict))
    return [residual_dict[h] for h in common], [price_dict[h] for h in common]


def fit_linear(x: list[float], y: list[float]) -> tuple[float, float]:
    """Closed-form 2-parameter OLS: y = a*x + b."""
    n = len(x)
    if n < 2 or n != len(y):
        raise ValueError("Need >=2 matching points.")
    sx, sy = sum(x), sum(y)
    sxx = sum(xi * xi for xi in x)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    denom = n * sxx - sx * sx
    if denom == 0:
        raise ValueError("Zero variance.")
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    return a, b


def predict_series(
    residual_dict: dict[str, float], a: float, b: float
) -> dict[str, float]:
    return {h: a * r + b for h, r in residual_dict.items()}


def merge_actual_and_predicted(
    actual: dict[str, float],
    predicted: dict[str, float],
    series_start: datetime,
    num_quarters: int,
) -> tuple[list[dict], dict[str, int]]:
    """Build a quarter-by-quarter list for [series_start, series_start + num_quarters * 15min).

    series_start is normally aligned to the start of the local day so the chart
    can render the full current day even before the current moment.

    Invariant: returns exactly `num_quarters` entries in chronological order,
    no gaps, no null prices. When neither `actual` nor `predicted` covers a
    quarter, forward-fill from the most recent predicted value. If the very
    first quarters have no predicted data either, look ahead for the first
    available predicted value to seed the fill.

    Returns `(series, stats)` where `stats` is a dict with:
      - `filled_quarters`: how many quarters were forward-filled from a
        previous predicted value (data thinning, not a hard outage).
      - `zero_seeded_quarters`: how many quarters fell back to 0.0 because
        neither actual nor any predicted value was available (hard outage —
        if > 0, surface this to the user via a sensor attribute).
    """
    keys = [(series_start + timedelta(minutes=15 * i)).isoformat() for i in range(num_quarters)]

    seed: float | None = None
    for key in keys:
        if key in predicted:
            seed = predicted[key]
            break

    out: list[dict] = []
    last_predicted: float | None = seed
    filled_quarters = 0
    zero_seeded_quarters = 0
    for key in keys:
        if key in actual:
            out.append({"start": key, "price": round(actual[key], 3), "source": "nordpool"})
            continue
        if key in predicted:
            last_predicted = predicted[key]
            out.append({"start": key, "price": round(last_predicted, 3), "source": "predicted"})
            continue
        if last_predicted is not None:
            out.append({"start": key, "price": round(last_predicted, 3), "source": "predicted"})
            filled_quarters += 1
            continue
        out.append({"start": key, "price": 0.0, "source": "predicted"})
        zero_seeded_quarters += 1
    stats = {
        "filled_quarters": filled_quarters,
        "zero_seeded_quarters": zero_seeded_quarters,
    }
    return out, stats


def build_forecast(
    nordpool_prices: Iterable[dict],
    wind_records: Iterable[dict],
    wind_actual_records: Iterable[dict],
    consumption_forecast_records: Iterable[dict],
    consumption_actual_records: Iterable[dict],
    series_start: datetime,
    series_end: datetime,
    default_slope: float,
    default_intercept: float,
    min_fit_samples: int,
) -> dict:
    """Run the full pipeline at 15-min resolution.

    Series spans [series_start, series_end), both normally aligned to local
    midnight so the dashboard always shows whole days. Quarters past the
    Fingrid forecast horizons are filled from the actual datasets one week
    back (same weekday + same quarter).
    """
    series_start = _quarter_floor(series_start)
    series_end = _quarter_floor(series_end)

    actual_prices = parse_price_sensor_attributes(nordpool_prices)
    wind_q = bucket_records(wind_records)
    wind_actual_q = bucket_records(wind_actual_records)
    cons_q = bucket_records(consumption_forecast_records)
    cons_actual_q = expand_hourly_to_quarters(consumption_actual_records)

    cons_q_extended = extend_with_last_week(cons_q, cons_actual_q, series_end)
    wind_q_extended = extend_with_last_week(wind_q, wind_actual_q, series_end)

    residual = {
        q: cons_q_extended[q] - wind_q_extended.get(q, 0.0) for q in cons_q_extended
    }

    xs, ys = align_series(actual_prices, residual)
    used_default = False
    if len(xs) >= min_fit_samples:
        try:
            a, b = fit_linear(xs, ys)
        except ValueError:
            a, b, used_default = default_slope, default_intercept, True
    else:
        a, b, used_default = default_slope, default_intercept, True

    predicted = predict_series(residual, a, b)

    num_quarters = max(0, int((series_end - series_start).total_seconds() // 900))
    series, fill_stats = merge_actual_and_predicted(
        actual_prices, predicted, series_start, num_quarters
    )

    return {
        "series": series,
        "slope": a,
        "intercept": b,
        "fit_samples": len(xs),
        "fit_used_default": used_default,
        "consumption_extended_quarters": len(cons_q_extended) - len(cons_q),
        "wind_extended_quarters": len(wind_q_extended) - len(wind_q),
        "filled_quarters": fill_stats["filled_quarters"],
        "zero_seeded_quarters": fill_stats["zero_seeded_quarters"],
    }

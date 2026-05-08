"""Pure prediction logic. Quarter keys are ISO8601 UTC strings (15-min floor)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable


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


def bucket_records(records: Iterable[dict]) -> dict[str, float]:
    """Bucket records by 15-min quarter. If multiple samples land in the same
    quarter (e.g. accidental higher-resolution input), take the mean.
    """
    buckets: dict[str, list[float]] = {}
    for r in records:
        start = r.get("startTime") or r.get("start_time") or r.get("start")
        val = r.get("value")
        if start is None or val is None:
            continue
        buckets.setdefault(_quarter_key(_parse_iso(start)), []).append(float(val))
    return {k: sum(vs) / len(vs) for k, vs in buckets.items() if vs}


def parse_price_sensor_attributes(prices: Iterable[dict]) -> dict[str, float]:
    """Parse Nord Pool style price entries to a quarter-keyed dict.

    Nord Pool moved to 15-minute MTU pricing in 2025; the source sensor's
    `prices` attribute is expected to expose 15-min entries. If multiple
    samples per quarter exist, take the mean.
    """
    buckets: dict[str, list[float]] = {}
    for p in prices:
        start = p.get("start") or p.get("startTime")
        price = p.get("price") or p.get("value")
        if start is None or price is None:
            continue
        buckets.setdefault(_quarter_key(_parse_iso(start)), []).append(float(price))
    return {k: sum(vs) / len(vs) for k, vs in buckets.items() if vs}


def expand_hourly_to_quarters(hourly_records: Iterable[dict]) -> dict[str, float]:
    """Expand hourly records into 4 quarter-keys per hour with the same value.

    Used for Fingrid datasets that are hourly resolution (e.g. 124, actual
    consumption) when the rest of the pipeline operates on 15-min quarters.
    """
    out: dict[str, float] = {}
    for r in hourly_records:
        start = r.get("startTime") or r.get("start_time") or r.get("start")
        val = r.get("value")
        if start is None or val is None:
            continue
        hour_dt = _parse_iso(start).replace(minute=0, second=0, microsecond=0)
        for q in range(4):
            qts = hour_dt + timedelta(minutes=15 * q)
            out[_quarter_key(qts)] = float(val)
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


def align_series(price_dict, residual_dict):
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


def predict_series(residual_dict, a, b):
    return {h: a * r + b for h, r in residual_dict.items()}


def merge_actual_and_predicted(actual, predicted, series_start, num_quarters):
    """Build a quarter-by-quarter list for [series_start, series_start + num_quarters * 15min).

    series_start is normally aligned to the start of the local day so the chart
    can render the full current day even before the current moment.
    """
    out = []
    for i in range(num_quarters):
        ts = series_start + timedelta(minutes=15 * i)
        key = ts.isoformat()
        if key in actual:
            out.append({"start": key, "price": round(actual[key], 3), "source": "nordpool"})
        elif key in predicted:
            out.append({"start": key, "price": round(predicted[key], 3), "source": "predicted"})
    return out


def build_forecast(
    nordpool_prices,
    wind_records,
    wind_actual_records,
    consumption_forecast_records,
    consumption_actual_records,
    series_start,
    series_end,
    default_slope,
    default_intercept,
    min_fit_samples,
    now=None,
):
    """Run the full pipeline at 15-min resolution.

    Series spans [series_start, series_end), both normally aligned to local
    midnight so the dashboard always shows whole days. Quarters past the
    Fingrid forecast horizons are filled from the actual datasets one week
    back (same weekday + same quarter).
    """
    if now is None:
        now = datetime.now(timezone.utc)
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
    series = merge_actual_and_predicted(
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
    }

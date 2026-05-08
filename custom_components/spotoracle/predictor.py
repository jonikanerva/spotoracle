"""Pure prediction logic. Hour keys are ISO8601 UTC strings."""
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


def _floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _hour_key(dt: datetime) -> str:
    return _floor_to_hour(dt).isoformat()


def aggregate_15min_to_hourly(records: Iterable[dict]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for r in records:
        start = r.get("startTime") or r.get("start_time") or r.get("start")
        val = r.get("value")
        if start is None or val is None:
            continue
        buckets.setdefault(_hour_key(_parse_iso(start)), []).append(float(val))
    return {k: sum(vs) / len(vs) for k, vs in buckets.items() if vs}


def parse_price_sensor_attributes(prices: Iterable[dict]) -> dict[str, float]:
    """Aggregate price entries (possibly 15-min) to hourly mean.

    Nord Pool moved to 15-minute MTU pricing in 2025; sensors may expose
    either hourly or 15-min entries. Bucketing by hour and taking the mean
    gives the right hourly value in both cases.
    """
    buckets: dict[str, list[float]] = {}
    for p in prices:
        start = p.get("start") or p.get("startTime")
        price = p.get("price") or p.get("value")
        if start is None or price is None:
            continue
        buckets.setdefault(_hour_key(_parse_iso(start)), []).append(float(price))
    return {k: sum(vs) / len(vs) for k, vs in buckets.items() if vs}


def extend_consumption_with_last_week(
    cons_forecast: dict[str, float],
    cons_actual: dict[str, float],
    horizon_end: datetime,
) -> dict[str, float]:
    """Fill missing hours after the forecast ends with same-weekday-same-hour
    values from one week ago. Returns a new dict (does not mutate input).
    """
    out = dict(cons_forecast)
    if not cons_forecast:
        return out
    last_known = _parse_iso(max(cons_forecast))
    cursor = last_known + timedelta(hours=1)
    while cursor < horizon_end:
        prev_week = cursor - timedelta(days=7)
        prev_key = _hour_key(prev_week)
        if prev_key in cons_actual:
            out[_hour_key(cursor)] = cons_actual[prev_key]
        cursor += timedelta(hours=1)
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


def merge_actual_and_predicted(actual, predicted, horizon_hours, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    start = _floor_to_hour(now)
    out = []
    for i in range(horizon_hours):
        ts = start + timedelta(hours=i)
        key = ts.isoformat()
        if key in actual:
            out.append({"start": key, "price": round(actual[key], 3), "source": "day_ahead"})
        elif key in predicted:
            out.append({"start": key, "price": round(predicted[key], 3), "source": "predicted"})
    return out


def build_forecast(
    nordpool_prices,
    wind_records,
    consumption_forecast_records,
    consumption_actual_records,
    horizon_hours,
    default_slope,
    default_intercept,
    min_fit_samples,
    now=None,
):
    """Run the full pipeline.

    consumption_actual_records is used to extend the forecast horizon by
    copying same-weekday-same-hour values from the past week.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    actual_prices = parse_price_sensor_attributes(nordpool_prices)
    wind_h = aggregate_15min_to_hourly(wind_records)
    cons_h = aggregate_15min_to_hourly(consumption_forecast_records)
    cons_actual_h = aggregate_15min_to_hourly(consumption_actual_records)

    horizon_end = _floor_to_hour(now) + timedelta(hours=horizon_hours)
    cons_h_extended = extend_consumption_with_last_week(
        cons_h, cons_actual_h, horizon_end
    )

    residual = {h: cons_h_extended[h] - wind_h.get(h, 0.0) for h in cons_h_extended}

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
    series = merge_actual_and_predicted(
        actual_prices, predicted, horizon_hours, now=now
    )
    return {
        "series": series,
        "slope": a,
        "intercept": b,
        "fit_samples": len(xs),
        "fit_used_default": used_default,
        "consumption_extended_hours": len(cons_h_extended) - len(cons_h),
    }

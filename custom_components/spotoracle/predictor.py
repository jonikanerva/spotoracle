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
    out: dict[str, float] = {}
    for p in prices:
        start = p.get("start") or p.get("startTime")
        price = p.get("price") or p.get("value")
        if start is None or price is None:
            continue
        out[_hour_key(_parse_iso(start))] = float(price)
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


def build_forecast(nordpool_prices, wind_records, consumption_records,
                   horizon_hours, default_slope, default_intercept, min_fit_samples):
    actual = parse_price_sensor_attributes(nordpool_prices)
    wind_h = aggregate_15min_to_hourly(wind_records)
    cons_h = aggregate_15min_to_hourly(consumption_records)
    residual = {h: cons_h[h] - wind_h.get(h, 0.0) for h in cons_h}

    xs, ys = align_series(actual, residual)
    used_default = False
    if len(xs) >= min_fit_samples:
        try:
            a, b = fit_linear(xs, ys)
        except ValueError:
            a, b, used_default = default_slope, default_intercept, True
    else:
        a, b, used_default = default_slope, default_intercept, True

    predicted = predict_series(residual, a, b)
    series = merge_actual_and_predicted(actual, predicted, horizon_hours)
    return {
        "series": series,
        "slope": a,
        "intercept": b,
        "fit_samples": len(xs),
        "fit_used_default": used_default,
    }

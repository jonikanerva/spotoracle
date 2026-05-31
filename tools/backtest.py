#!/usr/bin/env python3
"""Rolling-origin backtest for the SpotOracle price forecast.

Establishes a *measured baseline* for forecast quality before any model change.
The forecast's job is automation (picking cheap/expensive hours), so the report
leads with rank metrics (Spearman, precision@N for cheapest/most-expensive
hours) and treats MAE/RMSE/bias as reference. Accuracy is reported per forecast
horizon (day 1/2/3 ahead) because it degrades with horizon, and against naive
baselines the model must beat to justify itself — especially "last week same
quarter", which is literally what the predictor's tail extension already does.

Two levels:
  * Level A (model quality): feed the *actual* consumption/wind over the whole
    window as if they were perfect forecasts -> isolates the regression error.
  * Level B (operational): feed Fingrid forecasts + weekly extension exactly as
    production does -> the end-to-end error the user experiences.
The A-B gap says whether to invest next in the model or in the inputs.

No network, no API keys: reads the JSON files written by fetch_backtest_data.py.
Pure stdlib + the production predictor (imported, never modified).

Usage:
    python3 tools/backtest.py --data tools/backtest_data \
        --origin-hours 13,15 --levels A,B --floor off,on
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# tools/ is sys.path[0] when run as a script; tests insert it explicitly.
import metrics
from vintage import (
    HELSINKI,
    _parse,
    censor_actuals,
    censor_fingrid_forecast,
    censor_prices,
    forecast_window,
)

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "spotoracle")
)
from const import (  # noqa: E402
    DEFAULT_INTERCEPT,
    DEFAULT_SLOPE,
    FORECAST_DAYS,
    MIN_FIT_SAMPLES,
)
from predictor import (  # noqa: E402
    build_forecast,
    expand_hourly_to_quarters,
    quarter_key,
)

HORIZONS = ("day1", "day2", "day3")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_data(data_dir: Path) -> tuple[dict, list[dict], dict]:
    fingrid = json.loads((data_dir / "fingrid.json").read_text())
    prices = json.loads((data_dir / "prices.json").read_text())
    meta_path = data_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return fingrid, prices, meta


def price_records(prices_raw: list[dict], field: str) -> list[dict]:
    """Normalize stored price rows to predictor's {start, price} shape."""
    out = []
    for p in prices_raw:
        val = p.get(field)
        start = p.get("start")
        if val is None or start is None:
            continue
        out.append({"start": start, "price": float(val)})
    return out


def build_realized(records: list[dict]) -> dict[str, float]:
    """Quarter-key -> realized price, uncensored ground truth for scoring."""
    realized: dict[str, float] = {}
    for p in records:
        realized[quarter_key(_parse(p["start"]))] = p["price"]
    return realized


def _hourly_to_quarter_records(hourly: list[dict]) -> list[dict]:
    """Expand hourly Fingrid records to 15-min {startTime, value} records."""
    return [
        {"startTime": k, "value": v}
        for k, v in expand_hourly_to_quarters(hourly).items()
    ]


# ---------------------------------------------------------------------------
# Origins & inputs
# ---------------------------------------------------------------------------


def gen_origins(
    records: list[dict], origin_hours: list[int], tz: ZoneInfo
) -> list[datetime]:
    """One UTC origin T per (local day in the price span, origin hour)."""
    times = [_parse(p["start"]) for p in records]
    if not times:
        return []
    lo = min(times).astimezone(tz)
    hi = max(times).astimezone(tz)
    day = lo.replace(hour=0, minute=0, second=0, microsecond=0)
    origins: list[datetime] = []
    while day <= hi:
        for h in origin_hours:
            origins.append(day.replace(hour=h).astimezone(timezone.utc))
        day += timedelta(days=1)
    return origins


def build_inputs(
    fingrid: dict,
    cens_prices: list[dict],
    series_start: datetime,
    series_end: datetime,
    level: str,
    T: datetime,
) -> dict:
    """Assemble the build_forecast kwargs for a given level at origin T.

    Level B censors Fingrid to T-vintage (forecasts within horizon, actuals
    strictly before T). Level A intentionally feeds the *actual* datasets over
    the whole window as perfect forecasts — prices are still censored, so the
    model never sees the target it is scored against.
    """
    if level == "B":
        wind_fc = censor_fingrid_forecast(fingrid.get("245", []), T, 72)
        cons_fc = censor_fingrid_forecast(fingrid.get("165", []), T, 24)
        wind_act = censor_actuals(fingrid.get("75", []), T)
        cons_act = censor_actuals(fingrid.get("124", []), T)
    elif level == "A":
        wind_fc = fingrid.get("75", [])  # actual wind as a perfect forecast
        cons_fc = _hourly_to_quarter_records(fingrid.get("124", []))  # actual cons
        wind_act = fingrid.get("75", [])
        cons_act = fingrid.get("124", [])
    else:
        raise ValueError(f"unknown level {level!r}")
    return dict(
        nordpool_prices=cens_prices,
        wind_records=wind_fc,
        wind_actual_records=wind_act,
        consumption_forecast_records=cons_fc,
        consumption_actual_records=cons_act,
        series_start=series_start,
        series_end=series_end,
    )


def compute_floor(
    cens_prices: list[dict], T: datetime, days: int, pct: int
) -> float | None:
    """Per-origin floor: pct-th percentile of hourly minima over the trailing
    `days`, using only prices known at T. Mirrors the coordinator's LTS floor.
    """
    start = T - timedelta(days=days)
    by_hour: dict[datetime, list[float]] = {}
    for p in cens_prices:
        dt = _parse(p["start"])
        if start <= dt < T:
            hk = dt.replace(minute=0, second=0, microsecond=0)
            by_hour.setdefault(hk, []).append(p["price"])
    mins = sorted(min(v) for v in by_hour.values())
    if not mins:
        return None
    idx = max(0, int(len(mins) * pct / 100) - 1)
    return mins[idx]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def horizon_of(dt: datetime, series_start: datetime) -> str | None:
    hours = (dt - series_start).total_seconds() / 3600.0
    if 0 <= hours < 24:
        return "day1"
    if 24 <= hours < 48:
        return "day2"
    if 48 <= hours < 72:
        return "day3"
    return None


def score_series(
    series: list[dict], realized: dict[str, float]
) -> dict[str, list[tuple[datetime, float, float]]]:
    """Bucket (dt, predicted, actual) triples by horizon for one forecast run.

    Skips quarters with no realized price (partial window at the data edge).
    """
    if not series:
        return {h: [] for h in HORIZONS}
    series_start = _parse(series[0]["start"])
    buckets: dict[str, list[tuple[datetime, float, float]]] = {h: [] for h in HORIZONS}
    for point in series:
        key = point["start"]
        act = realized.get(key)
        if act is None:
            continue
        dt = _parse(key)
        h = horizon_of(dt, series_start)
        if h is not None:
            buckets[h].append((dt, point["price"], act))
    return buckets


def to_hourly(
    triples: list[tuple[datetime, float, float]], tz: ZoneInfo
) -> tuple[list[float], list[float]]:
    """Aggregate 15-min triples into hourly-mean (predicted, actual) lists."""
    by_hour: dict[datetime, list[tuple[float, float]]] = {}
    for dt, p, a in triples:
        hk = dt.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(hk, []).append((p, a))
    hours = sorted(by_hour)
    preds = [sum(x[0] for x in by_hour[h]) / len(by_hour[h]) for h in hours]
    acts = [sum(x[1] for x in by_hour[h]) / len(by_hour[h]) for h in hours]
    return preds, acts


# ---------------------------------------------------------------------------
# Baselines (model must beat these)
# ---------------------------------------------------------------------------


def baseline_series(
    name: str,
    series_keys: list[str],
    realized: dict[str, float],
    cens_prices: list[dict],
    T: datetime,
) -> list[dict]:
    """Build a naive predicted series over the same quarters as the model.

    Each baseline only uses prices that existed before T; a quarter with no
    legal source is omitted (coverage is reported, never back-filled).
    """
    if name == "flat":
        known = [p["price"] for p in cens_prices]
        if not known:
            return []
        mean = sum(known) / len(known)
        return [{"start": k, "price": mean} for k in series_keys]

    offset = timedelta(days=7) if name == "last_week" else timedelta(days=1)
    out: list[dict] = []
    for k in series_keys:
        src_dt = _parse(k) - offset
        if src_dt >= T:  # source not yet observed at origin
            continue
        src_val = realized.get(quarter_key(src_dt))
        if src_val is not None:
            out.append({"start": k, "price": src_val})
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _new_acc() -> dict:
    return {
        "pairs": [],  # pooled quarter (pred, act) for abs metrics
        "spearman": [],
        "p_cheap": [],
        "p_peak": [],
        "cls": [],
        "n_origins": 0,
        "n_hours": [],
    }


def accumulate(
    acc: dict,
    triples: list[tuple[datetime, float, float]],
    tz: ZoneInfo,
    cheap_n: int,
    peak_n: int,
) -> None:
    if not triples:
        return
    acc["pairs"].extend((p, a) for _, p, a in triples)
    preds, acts = to_hourly(triples, tz)
    if len(preds) < 2:
        return
    acc["n_origins"] += 1
    acc["n_hours"].append(len(preds))
    for key, val in (
        ("spearman", metrics.spearman(preds, acts)),
        ("p_cheap", metrics.precision_at_n(preds, acts, cheap_n, "cheap")),
        ("p_peak", metrics.precision_at_n(preds, acts, peak_n, "peak")),
        ("cls", metrics.classification_hit_rate(preds, acts)),
    ):
        if val is not None:
            acc[key].append(val)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarize(acc: dict) -> dict:
    pairs = acc["pairs"]
    preds = [p for p, _ in pairs]
    acts = [a for _, a in pairs]
    return {
        "spearman": _mean(acc["spearman"]),
        "p_cheap": _mean(acc["p_cheap"]),
        "p_peak": _mean(acc["p_peak"]),
        "cls": _mean(acc["cls"]),
        "mae": metrics.mae(preds, acts) if pairs else None,
        "rmse": metrics.rmse(preds, acts) if pairs else None,
        "bias": metrics.bias(preds, acts) if pairs else None,
        "n_origins": acc["n_origins"],
        "n_quarters": len(pairs),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _fmt(v: float | None, width: int = 7, dec: int = 2) -> str:
    return f"{v:>{width}.{dec}f}" if v is not None else f"{'n/a':>{width}}"


def _row(label: str, s: dict) -> str:
    return (
        f"  {label:<26}"
        f"{_fmt(s['spearman'])} {_fmt(s['p_cheap'])} {_fmt(s['p_peak'])} "
        f"{_fmt(s['cls'])} {_fmt(s['mae'])} {_fmt(s['rmse'])} {_fmt(s['bias'])} "
        f"{s['n_origins']:>5}"
    )


def print_report(
    results: dict,
    baselines: dict,
    levels: list[str],
    floors: list[str],
    args: argparse.Namespace,
    n_origins: int,
    meta: dict,
) -> None:
    header = (
        f"{'':<28}{'Spear':>7} {'P@'+str(args.cheap_n)+'c':>7} "
        f"{'P@'+str(args.peak_n)+'p':>7} {'cls':>7} {'MAE':>7} {'RMSE':>7} "
        f"{'bias':>7} {'orig':>5}"
    )
    print()
    print(
        f"SpotOracle backtest — {n_origins} origins, hours {args.origin_hours} EET, "
        f"price field: {args.price_field}"
    )
    if meta.get("window_start"):
        print(f"  data window: {meta['window_start']} .. {meta.get('window_end','?')}")
    print(
        f"  vintage: tomorrow known iff origin>={args.publication_hour}:00 EET; "
        "245 horizon=72h, 165=24h"
    )
    print(
        "  LIMITATION: Fingrid forecast values are archived finals, not true "
        "T-vintage -> Level B mildly optimistic on inputs."
    )

    for level in levels:
        for floor in floors:
            print()
            print(f"LEVEL {level} / floor {floor.upper()}")
            print(header)
            for h in HORIZONS:
                s = summarize(results[(level, floor, h)])
                print(_row(h, s))

    print()
    print("BASELINES (floor-independent; price field as above)")
    print(header)
    for name in ("last_week", "yesterday", "flat"):
        for h in HORIZONS:
            s = summarize(baselines[(name, h)])
            print(_row(f"{name} {h}", s))

    print()
    _print_verdict(results, baselines, levels)
    print()


def _print_verdict(results: dict, baselines: dict, levels: list[str]) -> None:
    floor = "off"
    msgs: list[str] = []
    if "B" in levels:
        b1 = summarize(results[("B", floor, "day1")])
        lw1 = summarize(baselines[("last_week", "day1")])
        if b1["spearman"] is not None and lw1["spearman"] is not None:
            delta = b1["spearman"] - lw1["spearman"]
            verb = "beats" if delta > 0 else "LOSES TO"
            msgs.append(
                f"day1 rank: model {verb} last-week baseline "
                f"(Spearman {b1['spearman']:.2f} vs {lw1['spearman']:.2f})."
            )
    if "A" in levels and "B" in levels:
        a1 = summarize(results[("A", floor, "day1")])
        b1 = summarize(results[("B", floor, "day1")])
        if a1["spearman"] is not None and b1["spearman"] is not None:
            gap = a1["spearman"] - b1["spearman"]
            where = "inputs (forecasts)" if gap > 0.1 else "the model"
            msgs.append(
                f"A-B day1 Spearman gap {gap:+.2f} -> headroom is mostly in {where}."
            )
    print("VERDICT: " + (" ".join(msgs) if msgs else "insufficient data."))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict:
    data_dir = Path(args.data)
    fingrid, prices_raw, meta = load_data(data_dir)
    recs = price_records(prices_raw, args.price_field)
    realized = build_realized(recs)
    origins = gen_origins(recs, args.origin_hours, HELSINKI)

    levels = args.levels
    floors = args.floor

    results = {
        (lv, fl, h): _new_acc() for lv in levels for fl in floors for h in HORIZONS
    }
    baselines = {(n, h): _new_acc() for n in ("last_week", "yesterday", "flat") for h in HORIZONS}

    used_origins = 0
    for T in origins:
        cens_prices = censor_prices(recs, T, args.publication_hour)
        series_start, series_end = forecast_window(cens_prices, T, args.forecast_days)
        # Skip origins whose window has no realized prices yet (data edge).
        end_key = quarter_key(series_end - timedelta(minutes=15))
        if end_key not in realized and quarter_key(series_start) not in realized:
            continue
        used_origins += 1

        floor_val = compute_floor(cens_prices, T, args.floor_days, args.floor_pct)

        for level in levels:
            inputs = build_inputs(
                fingrid, cens_prices, series_start, series_end, level, T
            )
            for fl in floors:
                fv = floor_val if fl == "on" else None
                result = build_forecast(
                    **inputs,
                    default_slope=DEFAULT_SLOPE,
                    default_intercept=DEFAULT_INTERCEPT,
                    min_fit_samples=MIN_FIT_SAMPLES,
                    floor=fv,
                )
                buckets = score_series(result["series"], realized)
                for h in HORIZONS:
                    accumulate(
                        results[(level, fl, h)],
                        buckets[h],
                        HELSINKI,
                        args.cheap_n,
                        args.peak_n,
                    )

        # Baselines: scored over the same quarters the model covers.
        series_keys = [
            (series_start + timedelta(minutes=15 * i)).isoformat()
            for i in range(int((series_end - series_start).total_seconds() // 900))
        ]
        for name in ("last_week", "yesterday", "flat"):
            bser = baseline_series(name, series_keys, realized, cens_prices, T)
            buckets = score_series(bser, realized)
            for h in HORIZONS:
                accumulate(
                    baselines[(name, h)], buckets[h], HELSINKI, args.cheap_n, args.peak_n
                )

    print_report(results, baselines, levels, floors, args, used_origins, meta)

    if args.json:
        out = {
            f"{lv}/{fl}/{h}": summarize(results[(lv, fl, h)])
            for lv in levels
            for fl in floors
            for h in HORIZONS
        }
        out.update(
            {f"baseline/{n}/{h}": summarize(baselines[(n, h)]) for n, h in baselines}
        )
        print(json.dumps(out, indent=2))
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SpotOracle rolling-origin backtest")
    p.add_argument("--data", default="tools/backtest_data", help="data directory")
    p.add_argument("--origin-hours", default="13,15", help="local origin hours, CSV")
    p.add_argument("--levels", default="A,B", help="A,B")
    p.add_argument("--floor", default="off,on", help="off,on")
    p.add_argument(
        "--price-field",
        default="totalCentsKwh",
        choices=["totalCentsKwh", "spotCentsKwh"],
    )
    p.add_argument("--cheap-n", type=int, default=8, help="cheapest-N hours metric")
    p.add_argument("--peak-n", type=int, default=4, help="most-expensive-N metric")
    p.add_argument("--forecast-days", type=int, default=FORECAST_DAYS)
    p.add_argument("--publication-hour", type=int, default=14)
    p.add_argument("--floor-days", type=int, default=30)
    p.add_argument("--floor-pct", type=int, default=5)
    p.add_argument("--json", action="store_true", help="also dump aggregates as JSON")
    ns = p.parse_args(argv)
    ns.origin_hours = [int(x) for x in str(ns.origin_hours).split(",") if x != ""]
    ns.levels = [x.strip() for x in str(ns.levels).split(",") if x.strip()]
    ns.floor = [x.strip() for x in str(ns.floor).split(",") if x.strip()]
    return ns


if __name__ == "__main__":
    run(parse_args())

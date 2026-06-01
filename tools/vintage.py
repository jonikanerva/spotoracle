"""Look-ahead firewall for the backtest harness.

This module is the single place where we reconstruct *what the integration
would legitimately have seen at a past forecast origin T*. Getting this wrong
silently inflates the measured accuracy, so every approximation lives here,
documented, with dedicated tests in ``tests/test_backtest.py``.

All times are UTC ``datetime`` internally; local-time reasoning (the day-ahead
publication cutoff and the "which local day" boundary) uses Europe/Helsinki and
happens only inside ``censor_prices`` / ``forecast_window``.

Three honesty rules, one per data role:

* **Prices** (fit target + ground truth): a price quarter is "known at T" only
  if its delivery falls before the day-ahead publication horizon. FI day-ahead
  prices for *tomorrow* publish ~14:00 local; before that, only today's already
  published prices are known. Gated on ``publication_hour`` (a knob, so the
  cutoff is sensitivity-testable).
* **Fingrid forecasts** (245 wind, 165 consumption): a forecast quarter is
  knowable at T only within its published horizon (``T + horizon_hours``).
  Beyond that it is dropped, so ``predictor.extend_with_last_week`` takes over
  exactly as in production. NOTE: Fingrid Open Data archives the *final* value
  per timestamp, not the true T-vintage snapshot, so within-horizon values are
  mildly optimistic — the harness reports this caveat rather than hiding it.
* **Actuals** (75 wind, 124 consumption): only observations strictly before T
  existed. This blocks the weekly extension from filling future quarters with
  not-yet-observed data.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Import the production predictor exactly the way tests/test_predictor.py does,
# so window math stays identical to the coordinator without duplicating it.
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "spotoracle")
)
from predictor import last_priced_quarter  # noqa: E402

HELSINKI = ZoneInfo("Europe/Helsinki")


def _parse(ts: str) -> datetime:
    """Parse an ISO8601 string to an aware UTC datetime (mirrors predictor)."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _record_time(record: dict) -> datetime:
    """UTC start time of a Fingrid record ({startTime}/{start_time}/{start})."""
    raw = record.get("startTime") or record.get("start_time") or record.get("start")
    return _parse(raw)


def _price_time(price: dict) -> datetime:
    """UTC start time of a price entry ({start}/{startTime})."""
    raw = price.get("start") or price.get("startTime")
    return _parse(raw)


def censor_prices(
    prices: list[dict],
    T: datetime,
    publication_hour: int = 14,
    tz: ZoneInfo = HELSINKI,
) -> list[dict]:
    """Keep only the price quarters published and known at origin ``T``.

    Always known: every quarter delivered before the end of T's local day
    (those prices published yesterday). Tomorrow's quarters are added only if
    ``T``'s local hour has reached ``publication_hour`` (day-ahead is out).
    """
    local_t = T.astimezone(tz)
    day_start = local_t.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = day_start + timedelta(days=1)
    if local_t.hour >= publication_hour:
        horizon_end = today_end + timedelta(days=1)  # tomorrow is published
    else:
        horizon_end = today_end  # only today's prices are known
    horizon_end_utc = horizon_end.astimezone(timezone.utc)
    return [p for p in prices if _price_time(p) < horizon_end_utc]


def censor_fingrid_forecast(
    records: list[dict], T: datetime, horizon_hours: float
) -> list[dict]:
    """Keep history (< T) plus forecast quarters within ``T + horizon_hours``.

    Quarters beyond the horizon are dropped so the predictor's weekly extension
    fills them, matching production where Fingrid simply has no forecast there.
    """
    cutoff = T + timedelta(hours=horizon_hours)
    return [r for r in records if _record_time(r) < cutoff]


def censor_actuals(records: list[dict], T: datetime) -> list[dict]:
    """Keep only actual observations strictly before ``T`` (the present)."""
    return [r for r in records if _record_time(r) < T]


def forecast_window(
    censored_prices: list[dict],
    T: datetime,
    forecast_days: int,
    tz: ZoneInfo = HELSINKI,
) -> tuple[datetime, datetime]:
    """Compute (series_start, series_end) the same way the coordinator does:
    one quarter after the last *known* published price, or local midnight of
    T's day if no prices are known yet.
    """
    last_priced = last_priced_quarter(censored_prices)
    if last_priced is not None:
        series_start = last_priced + timedelta(minutes=15)
    else:
        local_t = T.astimezone(tz)
        midnight = local_t.replace(hour=0, minute=0, second=0, microsecond=0)
        series_start = midnight.astimezone(timezone.utc)
    series_end = series_start + timedelta(days=forecast_days)
    return series_start, series_end

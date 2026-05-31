#!/usr/bin/env python3
"""Fetch historical data for the SpotOracle backtest.

This is the ONLY component that touches the network or API keys. Run it once
with your keys; it writes gitignored JSON the backtest then reads offline.

Two sources, two keys (either may be omitted to fetch only the other half):

* Fingrid Open Data (datasets 245/75/165/124) — same `/data` endpoint and
  `x-api-key` header the integration uses. Long windows are chunked in time and
  concatenated (deduped per datasetId+startTime) to stay under the page size.
  Key: --fingrid-key or env FINGRID_API_KEY.
* The user's own spot price API (price/history) — `Authorization: Bearer`.
  Each interval carries both spotCentsKwh and totalCentsKwh; we keep both so the
  backtest can score against the production fee basis (total) or pure spot.
  Key: --spot-key or env SPOT_API_KEY.

Stdlib only (urllib/json/argparse). No dependency on the predictor.

Usage:
    python3 tools/fetch_backtest_data.py \
        --from 2026-03-01 --to 2026-05-25 \
        --fingrid-key "$FINGRID_API_KEY" --spot-key "$SPOT_API_KEY" \
        --out tools/backtest_data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")
FINGRID_DATA_URL = "https://data.fingrid.fi/api/data"
FINGRID_DATASETS = [245, 75, 165, 124]
SPOT_HISTORY_URL = "https://spot.calmdonut.com/api/v1/price/history"


def _utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_midnight(date_str: str) -> datetime:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(tzinfo=HELSINKI)


USER_AGENT = "spotoracle-backtest/1.0 (+https://github.com/jonikanerva/spotoracle)"


def http_get_json(
    url: str, headers: dict, timeout: int = 90, retries: int = 5
) -> dict | list:
    # Set an explicit User-Agent: some WAFs reject the default "Python-urllib/*".
    hdrs = {"User-Agent": USER_AGENT, **headers}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # Fingrid Open Data rate-limits aggressively; honour Retry-After.
            if e.code == 429 and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After", 30))
                print(f"  429 rate-limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("retries exhausted")


# ---------------------------------------------------------------------------
# Fingrid
# ---------------------------------------------------------------------------


def fetch_fingrid(
    api_key: str, start: datetime, end: datetime, chunk_days: int
) -> dict[str, list[dict]]:
    """Fetch the four datasets over [start, end), chunked in time, split by id."""
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    seen: dict[str, set[str]] = {str(d): set() for d in FINGRID_DATASETS}
    out: dict[str, list[dict]] = {str(d): [] for d in FINGRID_DATASETS}

    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        params = urllib.parse.urlencode(
            {
                "datasets": ",".join(str(d) for d in FINGRID_DATASETS),
                "startTime": _utc_z(cursor),
                "endTime": _utc_z(chunk_end),
                "format": "json",
                "pageSize": 20000,
            }
        )
        url = f"{FINGRID_DATA_URL}?{params}"
        payload = http_get_json(url, headers)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for row in rows:
            ds = str(row.get("datasetId"))
            ts = row.get("startTime")
            if ds in out and ts is not None and ts not in seen[ds]:
                seen[ds].add(ts)
                out[ds].append(row)
        print(
            f"  fingrid {cursor.date()}..{chunk_end.date()}: +{len(rows)} rows",
            file=sys.stderr,
        )
        cursor = chunk_end
        if cursor < end:
            time.sleep(6)  # proactively stay under the rate limit (~10 req/min)
    return out


# ---------------------------------------------------------------------------
# Spot prices
# ---------------------------------------------------------------------------


def fetch_spot(
    api_key: str, from_date: str, to_date: str, chunk_days: int = 30
) -> list[dict]:
    """Fetch price/history and normalize to {start, totalCentsKwh, spotCentsKwh}.

    `start` is the interval's UTC deliveryStart (the predictor's quarter key).
    The window is chunked (the API rejects overly long ranges) and deduped.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    out: list[dict] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        params = urllib.parse.urlencode({"from": cur.isoformat(), "to": chunk_end.isoformat()})
        payload = http_get_json(f"{SPOT_HISTORY_URL}?{params}", headers)
        if isinstance(payload, dict):
            intervals = payload.get("prices", [])
        elif isinstance(payload, list):
            intervals = payload
        else:
            intervals = []
        added = 0
        for it in intervals:
            ts = it.get("deliveryStart") or it.get("start") or it.get("localStart")
            if ts is None or ts in seen:
                continue
            seen.add(ts)
            out.append(
                {
                    "start": ts,
                    "totalCentsKwh": it.get("totalCentsKwh"),
                    "spotCentsKwh": it.get("spotCentsKwh"),
                }
            )
            added += 1
        print(f"  spot {cur}..{chunk_end}: +{added} intervals", file=sys.stderr)
        cur = chunk_end + timedelta(days=1)
    print(f"  spot: {len(out)} intervals total", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch SpotOracle backtest data")
    p.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD (local)")
    p.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD (local, inclusive)")
    p.add_argument("--fingrid-key", default=os.environ.get("FINGRID_API_KEY"))
    p.add_argument("--spot-key", default=os.environ.get("SPOT_API_KEY"))
    p.add_argument("--out", default="tools/backtest_data")
    p.add_argument("--chunk-days", type=int, default=21)
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Fingrid window: local-midnight from .. local-midnight after `to` (inclusive).
    fg_start = _local_midnight(args.from_date)
    fg_end = _local_midnight(args.to_date) + timedelta(days=1)

    fingrid: dict[str, list[dict]] = {str(d): [] for d in FINGRID_DATASETS}
    prices: list[dict] = []

    if args.fingrid_key:
        print("Fetching Fingrid...", file=sys.stderr)
        try:
            fingrid = fetch_fingrid(args.fingrid_key, fg_start, fg_end, args.chunk_days)
        except urllib.error.HTTPError as e:
            print(f"Fingrid HTTP error {e.code}: {e.reason}", file=sys.stderr)
            return 2
    else:
        print("No Fingrid key -> writing empty fingrid.json", file=sys.stderr)

    if args.spot_key:
        print("Fetching spot prices...", file=sys.stderr)
        try:
            prices = fetch_spot(args.spot_key, args.from_date, args.to_date)
        except urllib.error.HTTPError as e:
            print(f"Spot HTTP error {e.code}: {e.reason}", file=sys.stderr)
            return 2
    else:
        print("No spot key -> writing empty prices.json", file=sys.stderr)

    (out_dir / "fingrid.json").write_text(json.dumps(fingrid))
    (out_dir / "prices.json").write_text(json.dumps(prices))
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "window_start": args.from_date,
                "window_end": args.to_date,
                "tz": "Europe/Helsinki",
                "price_count": len(prices),
                "fingrid_counts": {k: len(v) for k, v in fingrid.items()},
            },
            indent=2,
        )
    )
    print(
        f"Wrote {out_dir}/ — prices={len(prices)}, "
        f"fingrid={ {k: len(v) for k, v in fingrid.items()} }",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Data update coordinator."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY,
    CONF_FLOOR_SENSOR,
    CONF_PRICE_SENSOR,
    DATASET_CONSUMPTION_ACTUAL,
    DATASET_CONSUMPTION_FORECAST,
    DATASET_WIND_ACTUAL,
    DATASET_WIND_FORECAST_15MIN,
    DEFAULT_INTERCEPT,
    DEFAULT_SLOPE,
    DOMAIN,
    FINGRID_API_BASE,
    FLOOR_HISTORY_DAYS,
    FLOOR_PERCENTILE,
    FLOOR_REFRESH_INTERVAL,
    HISTORY_DAYS,
    MIN_FIT_SAMPLES,
    SERIES_DAYS,
    UPDATE_INTERVAL,
)
from .predictor import build_forecast

_LOGGER = logging.getLogger(__name__)


class SpotOracleCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self._api_key = entry.data[CONF_API_KEY]
        self.price_sensor = entry.data[CONF_PRICE_SENSOR]
        self._floor_sensor: str | None = entry.data.get(CONF_FLOOR_SENSOR) or None
        self._floor_cache: tuple[datetime, float | None] | None = None
        self._session = async_get_clientsession(hass)

    async def _fetch_datasets(
        self, dataset_ids: list[int], start: datetime, end: datetime
    ) -> dict[int, list[dict]]:
        """Fetch multiple datasets in a single request and split by datasetId."""
        url = f"{FINGRID_API_BASE}/data"
        params = {
            "datasets": ",".join(str(d) for d in dataset_ids),
            "startTime": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endTime": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "format": "json",
            "pageSize": 20000,
        }
        headers = {"x-api-key": self._api_key, "Accept": "application/json"}
        try:
            async with asyncio.timeout(30):
                async with self._session.get(url, params=params, headers=headers) as resp:
                    if resp.status in (401, 403):
                        raise UpdateFailed(f"Fingrid auth failed: {resp.status}")
                    if resp.status >= 400:
                        body = await resp.text()
                        raise UpdateFailed(
                            f"Fingrid HTTP {resp.status}: {body[:200]}"
                        )
                    payload = await resp.json()
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timeout fetching Fingrid data") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Network error: {err}") from err

        if not isinstance(payload, dict) or "data" not in payload:
            raise UpdateFailed("Unexpected Fingrid payload shape")

        out: dict[int, list[dict]] = {d: [] for d in dataset_ids}
        for entry in payload["data"]:
            ds = entry.get("datasetId")
            if ds in out:
                out[ds].append(entry)
        return out

    def _read_price_sensor(self) -> list[dict]:
        state = self.hass.states.get(self.price_sensor)
        if state is None:
            raise UpdateFailed(f"Price sensor {self.price_sensor} not found")
        prices = state.attributes.get("prices") or []
        return prices if isinstance(prices, list) else []

    async def _compute_floor_from_lts(self) -> float | None:
        """Compute the prediction floor from the configured floor_sensor's
        long-term hourly statistics.

        Returns the FLOOR_PERCENTILE-th percentile of hourly minimums over
        the past FLOOR_HISTORY_DAYS days. Returns None if the sensor is not
        configured, has no statistics history, or the recorder query fails.
        Failures never propagate — predictions just go un-clipped, matching
        v0.7.0 behaviour.
        """
        if self._floor_sensor is None:
            return None

        end = dt_util.utcnow()
        start = end - timedelta(days=FLOOR_HISTORY_DAYS)

        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                {self._floor_sensor},
                "hour",
                None,
                {"min"},
            )
        except Exception as err:  # noqa: BLE001  (defensive — keep prediction usable on any recorder failure)
            _LOGGER.warning(
                "Failed to query LTS for floor sensor %s: %s",
                self._floor_sensor,
                err,
            )
            return None

        rows = stats.get(self._floor_sensor, [])
        mins = sorted(
            row["min"] for row in rows if row.get("min") is not None
        )
        if not mins:
            _LOGGER.info(
                "Floor sensor %s has no LTS minimums for the last %d days; "
                "predictions will not be clipped",
                self._floor_sensor,
                FLOOR_HISTORY_DAYS,
            )
            return None

        idx = max(0, int(len(mins) * FLOOR_PERCENTILE / 100) - 1)
        return float(mins[idx])

    async def _resolve_floor(self) -> float | None:
        """Return the cached floor; refresh from LTS if cache is stale."""
        if self._floor_sensor is None:
            return None
        now = dt_util.utcnow()
        if self._floor_cache is not None:
            last_computed, cached = self._floor_cache
            if now - last_computed < FLOOR_REFRESH_INTERVAL:
                return cached
        floor = await self._compute_floor_from_lts()
        self._floor_cache = (now, floor)
        return floor

    async def _async_update_data(self) -> dict:
        # Series spans local midnight today → local midnight + SERIES_DAYS.
        # All Fingrid lookups bracket this window with HISTORY_DAYS of context
        # (for last-week extension) and 1 day of buffer at the end.
        local_now = dt_util.now()
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        series_start_utc = local_midnight.astimezone(timezone.utc)
        series_end_utc = (
            local_midnight + timedelta(days=SERIES_DAYS)
        ).astimezone(timezone.utc)

        fetch_start = series_start_utc - timedelta(days=HISTORY_DAYS)
        fetch_end = series_end_utc + timedelta(hours=24)

        datasets = await self._fetch_datasets(
            [
                DATASET_WIND_FORECAST_15MIN,
                DATASET_WIND_ACTUAL,
                DATASET_CONSUMPTION_FORECAST,
                DATASET_CONSUMPTION_ACTUAL,
            ],
            fetch_start,
            fetch_end,
        )
        wind = datasets[DATASET_WIND_FORECAST_15MIN]
        wind_actual = datasets[DATASET_WIND_ACTUAL]
        cons_forecast = datasets[DATASET_CONSUMPTION_FORECAST]
        cons_actual = datasets[DATASET_CONSUMPTION_ACTUAL]
        nordpool = self._read_price_sensor()

        floor = await self._resolve_floor()

        result = build_forecast(
            nordpool_prices=nordpool,
            wind_records=wind,
            wind_actual_records=wind_actual,
            consumption_forecast_records=cons_forecast,
            consumption_actual_records=cons_actual,
            series_start=series_start_utc,
            series_end=series_end_utc,
            default_slope=DEFAULT_SLOPE,
            default_intercept=DEFAULT_INTERCEPT,
            min_fit_samples=MIN_FIT_SAMPLES,
            floor=floor,
        )
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        _LOGGER.debug(
            "Fit: a=%.5f b=%.3f samples=%d default=%s cons_ext=%d wind_ext=%d "
            "floor=%s clipped=%d",
            result["slope"],
            result["intercept"],
            result["fit_samples"],
            result["fit_used_default"],
            result["consumption_extended_quarters"],
            result["wind_extended_quarters"],
            result["prediction_floor"],
            result["prediction_floor_clipped_quarters"],
        )
        return result

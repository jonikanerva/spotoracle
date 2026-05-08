"""Data update coordinator."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_KEY,
    CONF_PRICE_SENSOR,
    DATASET_CONSUMPTION_FORECAST,
    DATASET_WIND_FORECAST_15MIN,
    DEFAULT_INTERCEPT,
    DEFAULT_SLOPE,
    DOMAIN,
    FINGRID_API_BASE,
    FORECAST_HOURS,
    MIN_FIT_SAMPLES,
    UPDATE_INTERVAL,
)
from .predictor import build_forecast

_LOGGER = logging.getLogger(__name__)


class SpotOracleCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self._api_key = entry.data[CONF_API_KEY]
        self.price_sensor = entry.data[CONF_PRICE_SENSOR]
        self._session = async_get_clientsession(hass)

    async def _fetch_datasets(
        self, dataset_ids: list[int], start: datetime, end: datetime
    ) -> dict[int, list[dict]]:
        """Fetch multiple datasets in a single request and split by datasetId."""
        url = f"{FINGRID_API_BASE}/data"
        params = {
            "datasets": ",".join(str(d) for d in dataset_ids),
            "startTime": start.isoformat().replace("+00:00", "Z"),
            "endTime": end.isoformat().replace("+00:00", "Z"),
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
            _LOGGER.warning("Price sensor %s not found", self.price_sensor)
            return []
        prices = state.attributes.get("prices") or []
        return prices if isinstance(prices, list) else []

    async def _async_update_data(self) -> dict:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=24)
        end = now + timedelta(hours=FORECAST_HOURS)

        datasets = await self._fetch_datasets(
            [DATASET_WIND_FORECAST_15MIN, DATASET_CONSUMPTION_FORECAST],
            start,
            end,
        )
        wind = datasets[DATASET_WIND_FORECAST_15MIN]
        cons = datasets[DATASET_CONSUMPTION_FORECAST]
        nordpool = self._read_price_sensor()

        result = build_forecast(
            nordpool_prices=nordpool,
            wind_records=wind,
            consumption_records=cons,
            horizon_hours=FORECAST_HOURS,
            default_slope=DEFAULT_SLOPE,
            default_intercept=DEFAULT_INTERCEPT,
            min_fit_samples=MIN_FIT_SAMPLES,
        )
        _LOGGER.debug(
            "Fit: a=%.5f b=%.3f samples=%d default=%s",
            result["slope"],
            result["intercept"],
            result["fit_samples"],
            result["fit_used_default"],
        )
        return result

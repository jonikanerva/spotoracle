"""Config flow."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_PRICE_SENSOR,
    DATASET_WIND_FORECAST_15MIN,
    DOMAIN,
    FINGRID_API_BASE,
)

_LOGGER = logging.getLogger(__name__)


class SpotOracleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate_api_key(self, api_key: str) -> bool:
        """Probe Fingrid with a minimal request. Returns False only on 401/403.

        Network problems do not block registration; the user can still finish
        setup during transient Fingrid outages.
        """
        session = async_get_clientsession(self.hass)
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)
        params = {
            "datasets": str(DATASET_WIND_FORECAST_15MIN),
            "startTime": start.isoformat().replace("+00:00", "Z"),
            "endTime": end.isoformat().replace("+00:00", "Z"),
            "format": "json",
            "pageSize": 1,
        }
        headers = {"x-api-key": api_key, "Accept": "application/json"}
        try:
            async with asyncio.timeout(10):
                async with session.get(
                    f"{FINGRID_API_BASE}/data", params=params, headers=headers
                ) as resp:
                    if resp.status in (401, 403):
                        return False
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Fingrid validation skipped due to network error: %s", err)
            return True

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            price_sensor = user_input[CONF_PRICE_SENSOR]
            if not api_key:
                errors[CONF_API_KEY] = "required"
            state = self.hass.states.get(price_sensor)
            if state is None:
                errors[CONF_PRICE_SENSOR] = "entity_not_found"
            elif not isinstance(state.attributes.get("prices"), list):
                errors[CONF_PRICE_SENSOR] = "invalid_attributes"
            if not errors and not await self._validate_api_key(api_key):
                errors[CONF_API_KEY] = "invalid_api_key"
            if not errors:
                return self.async_create_entry(
                    title="SpotOracle",
                    data={CONF_API_KEY: api_key, CONF_PRICE_SENSOR: price_sensor},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_PRICE_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

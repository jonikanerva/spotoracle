"""Config flow."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import State
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_FLOOR_SENSOR,
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

    def _check_unit_match(
        self,
        price_state: State,
        floor_state: State,
        errors: dict[str, str],
    ) -> None:
        """Strict unit validation between price_sensor and floor_sensor.

        If both have a unit_of_measurement and they differ, reject. If either
        lacks a unit, allow with a warning — the user is responsible for
        keeping units consistent.
        """
        price_unit = price_state.attributes.get("unit_of_measurement")
        floor_unit = floor_state.attributes.get("unit_of_measurement")
        if price_unit is not None and floor_unit is not None:
            if price_unit != floor_unit:
                errors[CONF_FLOOR_SENSOR] = "unit_mismatch"
        else:
            _LOGGER.warning(
                "Could not verify unit match between price_sensor %s (unit=%r) "
                "and floor_sensor %s (unit=%r); user is responsible for unit "
                "consistency",
                price_state.entity_id,
                price_unit,
                floor_state.entity_id,
                floor_unit,
            )

    def _build_schema(self, defaults: dict) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_API_KEY,
                    description={"suggested_value": defaults.get(CONF_API_KEY)},
                ): str,
                vol.Required(
                    CONF_PRICE_SENSOR,
                    description={"suggested_value": defaults.get(CONF_PRICE_SENSOR)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    CONF_FLOOR_SENSOR,
                    description={"suggested_value": defaults.get(CONF_FLOOR_SENSOR)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )

    async def _async_handle_step(
        self, user_input: dict | None, *, is_reconfigure: bool
    ):
        errors: dict[str, str] = {}
        defaults: dict = {}
        entry = None
        if is_reconfigure:
            entry = self._get_reconfigure_entry()
            defaults = dict(entry.data)

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            price_sensor = user_input[CONF_PRICE_SENSOR]
            floor_sensor = user_input[CONF_FLOOR_SENSOR]

            if not api_key:
                errors[CONF_API_KEY] = "required"

            price_state = self.hass.states.get(price_sensor)
            if price_state is None:
                errors[CONF_PRICE_SENSOR] = "entity_not_found"
            elif not isinstance(price_state.attributes.get("prices"), list):
                errors[CONF_PRICE_SENSOR] = "invalid_attributes"

            floor_state = self.hass.states.get(floor_sensor)
            if floor_state is None:
                errors[CONF_FLOOR_SENSOR] = "entity_not_found"
            elif price_state is not None and CONF_PRICE_SENSOR not in errors:
                self._check_unit_match(price_state, floor_state, errors)

            if not errors and not await self._validate_api_key(api_key):
                errors[CONF_API_KEY] = "invalid_api_key"

            if not errors:
                data = {
                    CONF_API_KEY: api_key,
                    CONF_PRICE_SENSOR: price_sensor,
                    CONF_FLOOR_SENSOR: floor_sensor,
                }

                if is_reconfigure and entry is not None:
                    return self.async_update_reload_and_abort(entry, data=data)
                return self.async_create_entry(title="SpotOracle", data=data)

            # Re-render with the user's submitted values so they can correct
            # mistakes without retyping everything.
            defaults = {**defaults, **user_input}

        schema = self._build_schema(defaults)
        step_id = "reconfigure" if is_reconfigure else "user"
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self._async_handle_step(user_input, is_reconfigure=False)

    async def async_step_reconfigure(self, user_input=None):
        return await self._async_handle_step(user_input, is_reconfigure=True)

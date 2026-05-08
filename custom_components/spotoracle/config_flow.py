"""Config flow."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import CONF_API_KEY, CONF_PRICE_SENSOR, DOMAIN


class SpotOracleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
            if not errors:
                return self.async_create_entry(
                    title="SpotOracle (FI)",
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

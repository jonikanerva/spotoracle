"""SpotOracle integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .coordinator import SpotOracleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SpotOracleCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    @callback
    def _on_price_sensor_change(event) -> None:
        """Refresh as soon as the source price sensor's `prices` attribute
        changes — e.g. when Nord Pool publishes the next day's prices.
        Saves us waiting for the next 30-min poll cycle.
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return
        new_prices = new_state.attributes.get("prices")
        old_prices = old_state.attributes.get("prices") if old_state else None
        if new_prices != old_prices:
            _LOGGER.debug(
                "Source sensor %s prices attribute changed; refreshing",
                coordinator.price_sensor,
            )
            hass.async_create_task(coordinator.async_request_refresh())

    unsub = async_track_state_change_event(
        hass, [coordinator.price_sensor], _on_price_sensor_change
    )
    entry.async_on_unload(unsub)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

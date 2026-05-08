"""SpotOracle — single forecast sensor."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SENSOR_FORECAST
from .coordinator import SpotOracleCoordinator

DEFAULT_UNIT = "snt/kWh"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SpotOracleCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpotOracleForecastSensor(coordinator, entry)])


class SpotOracleForecastSensor(CoordinatorEntity[SpotOracleCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_attribution = "Fingrid Avoindata + Nord Pool"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "SpotOracle forecast"

    def __init__(self, coordinator: SpotOracleCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{SENSOR_FORECAST}"

    @property
    def native_unit_of_measurement(self):
        src = self.hass.states.get(self.coordinator.price_sensor)
        if src is not None:
            unit = src.attributes.get("unit_of_measurement")
            if unit:
                return unit
        return DEFAULT_UNIT

    @property
    def _series(self) -> list[dict]:
        return (self.coordinator.data or {}).get("series", [])

    @property
    def native_value(self):
        return self._series[0]["price"] if self._series else None

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.data or {}
        attrs = {
            "forecast": self._series,
            "slope": round(d.get("slope", 0.0), 6),
            "intercept": round(d.get("intercept", 0.0), 3),
            "fit_samples": d.get("fit_samples", 0),
            "fit_used_default": d.get("fit_used_default", True),
            "consumption_extended_quarters": d.get("consumption_extended_quarters", 0),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._series:
            attrs["source"] = self._series[0].get("source")
        return attrs

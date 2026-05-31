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
from .predictor import quarter_key

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
    def _current_point(self) -> dict | None:
        if not self._series:
            return None
        key = quarter_key(datetime.now(timezone.utc))
        return next(
            (s for s in self._series if s["start"] == key),
            self._series[0],
        )

    @property
    def native_value(self):
        point = self._current_point
        return point["price"] if point else None

    @property
    def extra_state_attributes(self) -> dict:
        # User-facing surface is intentionally minimal: the forecast itself,
        # when it was generated, and a single trust signal. `degraded` is True
        # when the price model fell back to default coefficients (uncalibrated)
        # or any quarter had no data (zero-filled) — the only states where the
        # forecast should not be trusted. Full numeric diagnostics (slope,
        # intercept, fit_samples, floor, extension counts) stay in the
        # coordinator's debug log, not on the entity.
        d = self.coordinator.data or {}
        degraded = bool(
            d.get("fit_used_default", True) or d.get("zero_seeded_quarters", 0) > 0
        )
        return {
            "forecast": self._series,
            "generated_at": d.get("generated_at"),
            "degraded": degraded,
        }

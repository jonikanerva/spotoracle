"""Tests for the SpotOracle config and reconfigure flows."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spotoracle.const import (
    CONF_API_KEY,
    CONF_FLOOR_SENSOR,
    CONF_PRICE_SENSOR,
    DOMAIN,
)


PRICE_SENSOR = "sensor.nordpool"
FLOOR_SENSOR = "sensor.current_price"


def _set_price_sensor(hass: HomeAssistant, *, with_prices: bool = True, unit: str | None = "c/kWh") -> None:
    attrs: dict = {"prices": [{"start": "2026-05-09T00:00:00+03:00", "price": 4.21}]} if with_prices else {"prices": "not-a-list"}
    if unit is not None:
        attrs["unit_of_measurement"] = unit
    hass.states.async_set(PRICE_SENSOR, "4.21", attrs)


def _set_floor_sensor(hass: HomeAssistant, *, unit: str | None = "c/kWh") -> None:
    attrs: dict = {}
    if unit is not None:
        attrs["unit_of_measurement"] = unit
    hass.states.async_set(FLOOR_SENSOR, "4.10", attrs)


@pytest.fixture
def mock_validate_api_key():
    with patch(
        "custom_components.spotoracle.config_flow.SpotOracleConfigFlow._validate_api_key",
        return_value=True,
    ) as mock:
        yield mock


async def test_user_flow_happy_path(hass: HomeAssistant, mock_validate_api_key) -> None:
    _set_price_sensor(hass)
    _set_floor_sensor(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_KEY: "test-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_API_KEY: "test-key",
        CONF_PRICE_SENSOR: PRICE_SENSOR,
        CONF_FLOOR_SENSOR: FLOOR_SENSOR,
    }


async def test_user_flow_rejects_invalid_api_key(hass: HomeAssistant) -> None:
    _set_price_sensor(hass)
    _set_floor_sensor(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.spotoracle.config_flow.SpotOracleConfigFlow._validate_api_key",
        return_value=False,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "bad-key",
                CONF_PRICE_SENSOR: PRICE_SENSOR,
                CONF_FLOOR_SENSOR: FLOOR_SENSOR,
            },
        )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_API_KEY: "invalid_api_key"}


async def test_user_flow_rejects_missing_price_sensor(hass: HomeAssistant, mock_validate_api_key) -> None:
    _set_floor_sensor(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_KEY: "test-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_PRICE_SENSOR: "entity_not_found"}


async def test_user_flow_rejects_price_sensor_without_prices_list(hass: HomeAssistant, mock_validate_api_key) -> None:
    _set_price_sensor(hass, with_prices=False)
    _set_floor_sensor(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_KEY: "test-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_PRICE_SENSOR: "invalid_attributes"}


async def test_user_flow_rejects_unit_mismatch(hass: HomeAssistant, mock_validate_api_key) -> None:
    _set_price_sensor(hass, unit="c/kWh")
    _set_floor_sensor(hass, unit="EUR/MWh")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_KEY: "test-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_FLOOR_SENSOR: "unit_mismatch"}


async def test_user_flow_allows_missing_unit_with_warning(hass: HomeAssistant, mock_validate_api_key) -> None:
    _set_price_sensor(hass, unit=None)
    _set_floor_sensor(hass, unit="c/kWh")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_KEY: "test-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY


async def test_user_flow_single_instance_only(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            CONF_API_KEY: "existing-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_flow_updates_data(hass: HomeAssistant, mock_validate_api_key) -> None:
    _set_price_sensor(hass)
    _set_floor_sensor(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            CONF_API_KEY: "old-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: "sensor.old_floor",
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_KEY: "new-key",
            CONF_PRICE_SENSOR: PRICE_SENSOR,
            CONF_FLOOR_SENSOR: FLOOR_SENSOR,
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {
        CONF_API_KEY: "new-key",
        CONF_PRICE_SENSOR: PRICE_SENSOR,
        CONF_FLOOR_SENSOR: FLOOR_SENSOR,
    }

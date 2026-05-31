"""Tests for SpotOracleCoordinator (I/O boundary)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.spotoracle.const import (
    CONF_API_KEY,
    CONF_FLOOR_SENSOR,
    CONF_PRICE_SENSOR,
    DATASET_CONSUMPTION_ACTUAL,
    DATASET_CONSUMPTION_FORECAST,
    DATASET_WIND_ACTUAL,
    DATASET_WIND_FORECAST_15MIN,
    DOMAIN,
    FINGRID_API_BASE,
)
from custom_components.spotoracle.coordinator import SpotOracleCoordinator


PRICE_SENSOR = "sensor.nordpool"
FLOOR_SENSOR = "sensor.current_price"


def _make_entry(
    *,
    floor_sensor: str | None = FLOOR_SENSOR,
) -> MockConfigEntry:
    data = {
        CONF_API_KEY: "test-key",
        CONF_PRICE_SENSOR: PRICE_SENSOR,
    }
    if floor_sensor is not None:
        data[CONF_FLOOR_SENSOR] = floor_sensor
    return MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data=data)


def _set_price_sensor(hass: HomeAssistant, prices: list | None = None) -> None:
    if prices is None:
        prices = [
            {"start": "2026-05-09T00:00:00+03:00", "price": 4.21},
            {"start": "2026-05-09T00:15:00+03:00", "price": 4.05},
        ]
    hass.states.async_set(
        PRICE_SENSOR,
        "4.21",
        {"prices": prices, "unit_of_measurement": "c/kWh"},
    )


def _empty_fingrid_payload() -> dict:
    """Fingrid /data response with no records — predictor uses defaults."""
    return {"data": []}


async def test_fetch_success_returns_full_forecast(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(
        f"{FINGRID_API_BASE}/data",
        json=_empty_fingrid_payload(),
    )
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    result = await coordinator._async_update_data()

    assert "series" in result
    assert len(result["series"]) == 288  # fixed 3-day predicted window
    assert "generated_at" in result
    # Predicted-only entries are exactly {start, price} — no source field.
    assert all(set(q) == {"start", "price"} for q in result["series"])


async def test_series_starts_after_last_published_price(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The source sensor publishes through 2026-05-09 00:15+03:00 (= 21:15Z).
    The forecast must begin one quarter later and never include a published
    quarter, for a fixed 3-day (288-quarter) window."""
    _set_price_sensor(hass)  # prices end at 00:15+03:00 = 2026-05-08T21:15Z
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    result = await coordinator._async_update_data()

    assert len(result["series"]) == 288
    assert result["series"][0]["start"] == "2026-05-08T21:30:00+00:00"
    assert all(set(q) == {"start", "price"} for q in result["series"])


async def test_no_published_prices_falls_back_to_local_midnight(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty price list must not crash: predict a full window from local
    midnight today instead of raising."""
    _set_price_sensor(hass, prices=[])
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    result = await coordinator._async_update_data()

    assert len(result["series"]) == 288
    assert all(set(q) == {"start", "price"} for q in result["series"])


async def test_fetch_auth_failure_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", status=401)
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    with pytest.raises(UpdateFailed, match="auth failed"):
        await coordinator._async_update_data()


async def test_fetch_server_error_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", status=500, text="bang")
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    with pytest.raises(UpdateFailed, match="HTTP 500"):
        await coordinator._async_update_data()


async def test_fetch_network_error_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(
        f"{FINGRID_API_BASE}/data",
        exc=aiohttp.ClientError("connection refused"),
    )
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    with pytest.raises(UpdateFailed, match="Network error"):
        await coordinator._async_update_data()


async def test_unexpected_payload_shape_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=["not-a-dict"])
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    with pytest.raises(UpdateFailed, match="Unexpected Fingrid payload"):
        await coordinator._async_update_data()


async def test_missing_price_sensor_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    with pytest.raises(UpdateFailed, match="Price sensor"):
        await coordinator._async_update_data()


async def test_fetch_splits_payload_by_dataset_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(
        f"{FINGRID_API_BASE}/data",
        json={
            "data": [
                {"datasetId": DATASET_WIND_FORECAST_15MIN, "startTime": "x", "value": 1},
                {"datasetId": DATASET_WIND_ACTUAL, "startTime": "x", "value": 2},
                {"datasetId": DATASET_CONSUMPTION_FORECAST, "startTime": "x", "value": 3},
                {"datasetId": DATASET_CONSUMPTION_ACTUAL, "startTime": "x", "value": 4},
                {"datasetId": 999, "startTime": "x", "value": 5},
            ]
        },
    )
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=1)
    splits = await coordinator._fetch_datasets(
        [
            DATASET_WIND_FORECAST_15MIN,
            DATASET_WIND_ACTUAL,
            DATASET_CONSUMPTION_FORECAST,
            DATASET_CONSUMPTION_ACTUAL,
        ],
        start,
        end,
    )
    assert len(splits[DATASET_WIND_FORECAST_15MIN]) == 1
    assert len(splits[DATASET_WIND_ACTUAL]) == 1
    assert len(splits[DATASET_CONSUMPTION_FORECAST]) == 1
    assert len(splits[DATASET_CONSUMPTION_ACTUAL]) == 1
    assert 999 not in splits


async def test_floor_sensor_none_skips_lts_query(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    floor = await coordinator._compute_floor_from_lts()
    assert floor is None


async def test_floor_recorder_failure_returns_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)
    with patch(
        "custom_components.spotoracle.coordinator.get_instance",
        side_effect=RuntimeError("recorder unavailable"),
    ):
        floor = await coordinator._compute_floor_from_lts()
    assert floor is None


async def test_floor_empty_lts_history_returns_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """When the recorder returns no rows, _compute_floor_from_lts must
    short-circuit to None before the percentile math runs."""
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)

    class _EmptyRecorder:
        async def async_add_executor_job(self, _func, *_args):
            return {FLOOR_SENSOR: []}

    with patch(
        "custom_components.spotoracle.coordinator.get_instance",
        return_value=_EmptyRecorder(),
    ):
        floor = await coordinator._compute_floor_from_lts()
    assert floor is None


async def test_floor_percentile_computation(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)

    fake_rows = [{"min": v} for v in range(1, 101)]  # 1..100

    class _StubRecorder:
        async def async_add_executor_job(self, _func, *_args):
            return {FLOOR_SENSOR: fake_rows}

    with patch(
        "custom_components.spotoracle.coordinator.get_instance",
        return_value=_StubRecorder(),
    ):
        floor = await coordinator._compute_floor_from_lts()

    # FLOOR_PERCENTILE = 5 → index = max(0, int(100 * 5/100) - 1) = 4 → rows[4] = 5.
    assert floor == 5.0


async def test_floor_cache_returns_within_refresh_interval(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = SpotOracleCoordinator(hass, entry)

    call_count = 0

    async def _stub_compute(self):
        nonlocal call_count
        call_count += 1
        return 7.0

    with patch.object(
        SpotOracleCoordinator, "_compute_floor_from_lts", _stub_compute
    ):
        first = await coordinator._resolve_floor()
        second = await coordinator._resolve_floor()
    assert first == 7.0
    assert second == 7.0
    assert call_count == 1


async def test_full_setup_creates_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """End-to-end: config entry → coordinator first refresh → sensor entity."""
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch.object(
        SpotOracleCoordinator, "_compute_floor_from_lts", return_value=None
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(f"sensor.spotoracle_forecast")
    assert state is not None
    assert state.attributes.get("forecast") is not None
    assert len(state.attributes["forecast"]) == 288
    assert state.attributes.get("unit_of_measurement") == "c/kWh"
    # v2.0 minimal attribute surface: forecast, generated_at, degraded only.
    assert "generated_at" in state.attributes
    assert "degraded" in state.attributes
    # No Fingrid data + no model fit in this setup → forecast is degraded.
    assert state.attributes["degraded"] is True
    assert "source" not in state.attributes
    assert "slope" not in state.attributes
    assert "prediction_floor" not in state.attributes


async def test_setup_without_floor_sensor_raises_config_entry_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """v0.7.2 made floor_sensor required; legacy entries must surface as error."""
    _set_price_sensor(hass)
    aioclient_mock.get(f"{FINGRID_API_BASE}/data", json=_empty_fingrid_payload())
    entry = _make_entry(floor_sensor=None)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.recoverable  # ConfigEntryError → setup_retry/error state

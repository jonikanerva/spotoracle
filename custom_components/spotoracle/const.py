"""Constants for SpotOracle."""
from datetime import timedelta

DOMAIN = "spotoracle"

CONF_API_KEY = "api_key"
CONF_PRICE_SENSOR = "price_sensor"

UPDATE_INTERVAL = timedelta(minutes=30)

FINGRID_API_BASE = "https://data.fingrid.fi/api"
DATASET_WIND_FORECAST_15MIN = 245       # tuulivoimaennuste, 15 min, ~72h
DATASET_CONSUMPTION_FORECAST = 165      # kulutusennuste, hourly, 24h+
DATASET_CONSUMPTION_ACTUAL = 124        # toteutunut kulutus, hourly (käytetään ekstrapolointiin)

HISTORY_DAYS = 8                        # kuinka monta päivää historiaa haetaan ekstrapolointia varten

FORECAST_HOURS = 72
MIN_FIT_SAMPLES = 6

DEFAULT_SLOPE = 0.0020       # snt/kWh per MW residual
DEFAULT_INTERCEPT = -2.0     # snt/kWh

SENSOR_FORECAST = "forecast"

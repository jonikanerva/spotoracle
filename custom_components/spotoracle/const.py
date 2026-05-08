"""Constants for SpotOracle."""
from datetime import timedelta

DOMAIN = "spotoracle"

CONF_API_KEY = "api_key"
CONF_PRICE_SENSOR = "price_sensor"

UPDATE_INTERVAL = timedelta(minutes=30)

FINGRID_API_BASE = "https://data.fingrid.fi/api"
DATASET_WIND_FORECAST_15MIN = 245       # wind power forecast, 15 min, ~72h
DATASET_WIND_ACTUAL = 75                # actual wind power, 15 min (used for last-week extension)
DATASET_CONSUMPTION_FORECAST = 165      # consumption forecast, 15 min, ~24h
DATASET_CONSUMPTION_ACTUAL = 124        # actual consumption, hourly (used for last-week extension)

HISTORY_DAYS = 8                        # how many days of history to fetch for last-week extension
SERIES_DAYS = 4                         # series spans local-midnight + N days (= 4 × 96 = 384 quarters)

MIN_FIT_SAMPLES = 24             # quarters; 24 × 15 min = 6h minimum overlap

DEFAULT_SLOPE = 0.0020       # snt/kWh per MW residual
DEFAULT_INTERCEPT = -2.0     # snt/kWh

SENSOR_FORECAST = "forecast"

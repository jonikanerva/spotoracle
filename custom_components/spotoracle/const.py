"""Constants for SpotOracle."""
from datetime import timedelta

DOMAIN = "spotoracle"

CONF_API_KEY = "api_key"
CONF_PRICE_SENSOR = "price_sensor"
CONF_FLOOR_SENSOR = "floor_sensor"

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

# Optional prediction floor: derived from a user-supplied "current price" sensor's
# long-term statistics. The 5th percentile of hourly minimums over the last 30 days
# acts as a robust lower bound, preventing OLS extrapolation past the observed price
# range. A 30-day rolling window tracks Finnish seasonal price variation (winter
# 10–30+ snt vs. summer 1–8 snt); longer windows would mix seasons and over-floor
# in spring. Without the optional sensor configured, no clipping is applied.
FLOOR_HISTORY_DAYS = 30
FLOOR_PERCENTILE = 5
FLOOR_REFRESH_INTERVAL = timedelta(hours=24)

SENSOR_FORECAST = "forecast"

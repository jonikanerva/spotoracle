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
DATASET_CONSUMPTION_ACTUAL = 124        # actual consumption, 15-min since the 2025 MTU shift (used for last-week extension)

HISTORY_DAYS = 29                       # days of history to fetch: covers WIND_EXTENSION_WEEKS (+1 day margin)
FORECAST_DAYS = 3                       # predicted series length: fixed N days after the last published
                                        # price (= 3 × 96 = 288 quarters), no published-price pass-through

MIN_FIT_SAMPLES = 24             # quarters; 24 × 15 min = 6h minimum overlap

# Tail extension past Fingrid's forecast horizons (same weekday/quarter, N weeks
# back). Consumption stays at 1 (strong weekly cycle). Wind has no weekly cycle,
# so it averages over more weeks toward climatology — measurably better on the
# day-2/3 tail (see tools/backtest.py).
WIND_EXTENSION_WEEKS = 4

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

<p align="center">
  <img src="https://raw.githubusercontent.com/jonikanerva/spotoracle/main/icons/icon.png" alt="SpotOracle" width="128" height="128"/>
</p>

# SpotOracle

A Home Assistant integration that forecasts **future** electricity prices for the Finnish FI bidding zone — the days your Nord Pool day-ahead sensor does **not yet** cover. It learns the price/load relationship from your existing day-ahead price sensor and projects it forward using Fingrid Open Data forecasts (wind power + consumption). Heuristic linear regression — no numpy/pandas/ML dependencies.

## What you get

A single sensor `sensor.spotoracle_forecast` whose `forecast` attribute is a list of `{start, price}` entries at **15-minute resolution**. The series begins one quarter **after the last price your source sensor publishes** — it never duplicates prices you already have — and runs a fixed **3 days forward = exactly 288 entries**, in chronological order, with no gaps and no null prices. Plugs directly into the ApexCharts card's `data_generator`.

## Source price sensor requirements

You need an existing HA sensor whose `prices` attribute is a list of 15-minute entries:

```yaml
prices:
  - start: "2026-05-08T00:00:00+03:00" # ISO8601 (local or UTC), 15-min steps
    price: 4.21 # c/kWh
  - start: "2026-05-08T00:15:00+03:00"
    price: 4.05
  - start: "2026-05-08T00:30:00+03:00"
    price: 3.92
  - start: "2026-05-08T00:45:00+03:00"
    price: 3.87
```

> Nord Pool moved to 15-minute MTU pricing (Market Time Unit) in 2025; use a source sensor that exposes 15-min price entries.

Compatible sources include:

- Nord Pool style HACS integrations that expose a `prices` list.
- A custom REST sensor pulling from `api.spot-hinta.fi` or `elering.ee`.
- A template sensor that reshapes another integration's prices into this format.

### Unit

The forecast unit is inherited from the source sensor's `unit_of_measurement` attribute. If your sensor reports `c/kWh`, the forecast is in `c/kWh`. If it reports `EUR/MWh`, the forecast comes out in the same unit — as long as the attribute is set.

### Fees and transmission tariffs

The regression fits coefficients directly against the source sensor's values, so the forecast **automatically inherits the same fees** as the source sensor. If your sensor already exposes a "total price" (including transmission, margin, taxes), the forecast is a total-price forecast. If your sensor is pure spot, the forecast is pure spot.

**Time-of-day fees**: a clear time-of-day fee pattern (e.g. night tariff 22–07 / day tariff 07–22) is largely captured by the per-hour-of-day bias correction (see "How the forecast is computed", step 7), which learns the hourly offset from your published prices rather than averaging it away. A residual mismatch can remain right at tariff boundaries or when the pattern shifts seasonally; if you need exact tariff-aware pricing, encode the time-of-day rates into the source sensor itself (e.g. via a template sensor) so the forecast inherits them directly.

## Prediction floor (required)

The OLS regression is linear and has no built-in lower bound. When wind generation is high and consumption is low (typical windy night), the linear fit extrapolates predicted prices to zero or below — physically impossible for any consumer who pays transmission fees, taxes, or VAT.

To prevent this, the integration **requires** a second sensor: **Current price sensor for floor calibration**. Pick a sensor that records your effective current electricity price (e.g. `sensor.nordpool_price_now_with_fees_snt_kwh`), with `state_class: measurement` so Home Assistant captures it into long-term statistics. The sensor's `unit_of_measurement` must match the source price sensor's; the config flow rejects mismatched units to prevent silently distorted floors.

The integration queries that sensor's hourly statistics from the last 30 days, takes the **5th percentile of the hourly minimums** as the floor, and clips any predicted quarter that would fall below this value. The 30-day rolling window naturally tracks Finnish seasonal price variation (winter prices ≫ summer prices); the 5th percentile filters out single-point measurement glitches. The floor is recomputed every 24 hours.

If you are upgrading from v0.7.0 or v0.7.1 (where the floor sensor was either absent or optional), Home Assistant will surface the entry as needing reconfiguration after the update. Open **Settings → Devices & services → SpotOracle → ⋮ → Reconfigure**, set the floor sensor, and save — your existing settings and price history are preserved.

## Installation (HACS)

1. Open **HACS** → ⋮ → **Custom repositories**.
2. Paste the URL `https://github.com/jonikanerva/spotoracle`, category **Integration** → **Add**.
3. Find **"SpotOracle"** in the list → **Download** → restart Home Assistant.
4. **Settings → Devices & services → Add integration → "SpotOracle"**.
5. Get a [Fingrid Open Data API key](https://developer-data.fingrid.fi/apis) (free, email registration only). In the setup form, pick both your **source price sensor** (the day-ahead one with the `prices` attribute) and your **current price sensor for floor calibration** (a `state_class: measurement` sensor recording your effective current price; both must use the same `unit_of_measurement`).

## Sensor attributes

| Attribute      | Meaning                                                                                                                                                                                                                                                              |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `forecast`     | List of `{start, price}` at **15-min resolution**: a fixed 3-day (288-entry) window starting one quarter after your last published price. Use as the `data_generator` input for ApexCharts.                                                                          |
| `generated_at` | UTC timestamp marking when the forecast was computed.                                                                                                                                                                                                                 |
| `degraded`     | `true` when the forecast should **not** be trusted: either the price model could not be fitted (too little overlap with your source sensor → generic default coefficients) or some quarters had no Fingrid data and were zero-filled. Normally `false`.                |

The forecast's unit is inherited from the source sensor's `unit_of_measurement` (exposed by Home Assistant as the entity's own `unit_of_measurement`). Detailed numeric diagnostics — regression coefficients, sample count, prediction floor, and last-week extension counts — are written to Home Assistant's debug log for `custom_components.spotoracle` rather than carried as entity attributes.

## Technical notes

### Native 15-min resolution

Nord Pool moved to 15-minute price periods (MTU = Market Time Unit) in 2025. The integration runs natively at 15-min resolution — both the regression fit and the forecast output operate in 15-min steps. The `forecast` attribute always contains **exactly 288 entries spanning a fixed 3-day window**.

For ApexCharts, the correct visualization for 15-min prices is **stepline** (a step function), not a smooth line: each quarter holds a flat price for its full duration, with sharp transitions between quarters. See the example below.

### Fixed 3-day window after published prices

The forecast does **not** duplicate prices your source sensor already publishes. It begins one 15-min quarter after the **last** published price and runs a fixed **3 days = 288 quarters** forward — so the first predicted quarter is the start of the first day your source sensor does not yet cover (typically tomorrow, or the day after once tomorrow's day-ahead prices are published around 14:00–15:00 EET). The window length is constant regardless of how much your sensor covers.

Fingrid's own forecast horizons are shorter than 3 days: the wind power forecast (245) extends ~72h and the consumption forecast (165) ~24h. The remaining quarters are filled from **last week's actuals** at the same weekday/quarter pair:

- **Consumption** (dataset 124, 15-min resolution) → when Fingrid's consumption forecast ends.
- **Wind power** (dataset 75, 15 min) → when Fingrid's wind power forecast ends.

The Finnish electricity-consumption weekly cycle is strong, so consumption extrapolation is accurate. Wind power varies with weather, making the same hour one week ago a coarser proxy. When the window already starts after tomorrow's published prices, its later quarters lean entirely on this same-weekday-last-week extension — the same deliberate approximation the integration has always used for its multi-day tail, fine for automations.

The number of extrapolated quarters is written to the debug log (`cons_ext` / `wind_ext`) for `custom_components.spotoracle`.

## ApexCharts card

Requires the [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) card from HACS (Frontend category).

The example below covers all the essentials:

- **This sensor's 3-day forecast** as color-coded bars (`graph_span: 5d` comfortably fits a window that may start the day after tomorrow).
- **Color coding by price level**: green < 15 c/kWh, yellow 15–30, red ≥ 30.
- **"Now" marker** as a ▼ glyph — the forecast bars sit to its right, in the future.

```yaml
type: custom:apexcharts-card
grid_options:
  columns: full
graph_span: 5d
span:
  start: day
now:
  show: true
  color: yellow
  label: ▼
apex_config:
  legend:
    show: false
  chart:
    height: 250
  xaxis:
    type: datetime
    labels:
      datetimeUTC: false
    crosshairs:
      show: false
    tooltip:
      enabled: false
  tooltip:
    x:
      format: dd.MM.yyyy HH:mm
  plotOptions:
    bar:
      columnWidth: 100%
      colors:
        ranges:
          - from: -1000
            to: 14.999
            color: "#22c55e"
          - from: 15
            to: 29.999
            color: "#f59e0b"
          - from: 30
            to: 10000
            color: "#ef4444"
yaxis:
  - id: price
    decimals: 0
series:
  - entity: sensor.spotoracle_forecast
    yaxis_id: price
    type: column
    name: Forecast
    data_generator: |
      return entity.attributes.forecast
        .map(p => [new Date(p.start).getTime(), p.price]);
```

The card shows **this sensor's 3-day forecast** as color-coded bars. Green = cheap, yellow = mid-range, red = expensive. The yellow ▼ marks the current moment on the axis; the forecast bars sit to its right, covering the days your own price sensor does not yet reach.

> Colors are defined via ApexCharts' native `plotOptions.bar.colors.ranges`, not the apexcharts-card wrapper's `color_threshold` — this is the most reliable way to get distinct per-bar colors without bleeding into a gradient at 15-min resolution.

## How the forecast is computed

1. Read published day-ahead prices from the source sensor's `prices` attribute (15-min entries).
2. Fetch four datasets from Fingrid Open Data in a single `/api/data` call:
   - **245** — wind power forecast (15 min, ~72h).
   - **75** — actual wind power (15 min, used to extrapolate the forecast).
   - **165** — consumption forecast (15 min, ~24h).
   - **124** — actual consumption (15-min since the 2025 MTU shift; hourly input is still handled for backward compatibility).
3. Bucket into 15-min quarters → compute `residual = consumption − wind` per quarter.
4. For quarters with **both a published price and a Fingrid forecast**, fit a linear regression `price = a · residual + b`.
5. When Fingrid's own forecasts end, **extrapolate both consumption and wind power from last week's actuals** (same weekday + same quarter).
6. Apply the coefficients to every quarter starting one step **after the last published price**, running a fixed 3 days forward.
7. **Add a per-hour-of-day bias correction** learned from your published prices (the mean of `actual − predicted` grouped by UTC hour). The linear residual model captures the overall price *level* but not the daily price *rhythm* — morning/evening peaks, night troughs — which is driven by factors outside Finnish consumption and wind. This additive correction recovers that rhythm.
8. Output a predicted-only **3 × 96 = 288-point** series, with no gaps and no null prices. Your published prices are the fit target only — they are never passed back through the forecast.

### Update frequency

- **Polling** Fingrid every 30 minutes. This is the upper bound on daily requests: ~144 requests/day per dataset, but all four are fetched in a single HTTP call → about 48 calls/day (the limit is 10,000).
- **Reactive refresh**: the integration listens for source-sensor state changes and refreshes itself **immediately** when the source's `prices` attribute changes. So when Nord Pool publishes tomorrow's prices around 14:00–15:00 EET and your source sensor picks them up, SpotOracle gets them within seconds — no waiting for the next 30-min cycle.

## HACS icon visibility

The icons under `icons/` in this repo are **not picked up automatically** by HACS in the integration listing — HACS reads icons from the official [`home-assistant/brands`](https://github.com/home-assistant/brands) repository. To get the icon shown in the HACS listing, open a pull request against Brands that adds:

- `custom_integrations/spotoracle/icon.png` (256×256, this repo's `icons/icon.png`).
- `custom_integrations/spotoracle/icon@2x.png` (512×512, this repo's `icons/icon@2x.png`).

Instructions: <https://github.com/home-assistant/brands/blob/master/README.md>. Approval usually takes a few days. While waiting, HACS shows the default icon, but the integration works normally otherwise.

## License

MIT.

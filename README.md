<p align="center">
  <img src="https://raw.githubusercontent.com/jonikanerva/spotoracle/main/icon.png" alt="SpotOracle" width="128" height="128"/>
</p>

# SpotOracle

A Home Assistant integration that produces a **0–96h electricity-price forecast** for the Finnish FI bidding zone by combining your existing Nord Pool day-ahead price sensor with Fingrid Open Data forecasts (wind power + consumption). Heuristic linear regression — no numpy/pandas/ML dependencies.

## What you get

A single sensor `sensor.spotoracle_forecast` whose `forecast` attribute is a list of `{start, price, source}` entries at **15-minute resolution**, spanning local midnight today through 4 days ahead. The series always contains exactly **384 entries in chronological order, with no gaps and no null prices**. `source` is `nordpool` (published price from your source sensor) or `predicted` (heuristic forecast). Plugs directly into the ApexCharts card's `data_generator`.

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

**Limitation**: if the fee structure has clear time-of-day dependence (e.g. night tariff 22–07 / day tariff 07–22), linear regression absorbs only the average — individual night hours can be off by 1–3 c/kWh due to mean bias. Sufficient for most automations; an hour-of-day bias correction is planned for a later release.

## Installation (HACS)

1. Open **HACS** → ⋮ → **Custom repositories**.
2. Paste the URL `https://github.com/jonikanerva/spotoracle`, category **Integration** → **Add**.
3. Find **"SpotOracle"** in the list → **Download** → restart Home Assistant.
4. **Settings → Devices & services → Add integration → "SpotOracle"**.
5. Get a [Fingrid Open Data API key](https://developer-data.fingrid.fi/apis) (free, email registration only) and pick your source sensor from the dropdown.

## Sensor attributes

| Attribute                       | Meaning                                                                                                                                                                       |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `forecast`                      | List of `{start, price, source}` at **15-min resolution** from local midnight today → +4 days = 384 entries. Use as the `data_generator` input for ApexCharts.                |
| `source`                        | Source for the current 15-min point: `nordpool` or `predicted`.                                                                                                               |
| `slope`, `intercept`            | Regression coefficients `price = slope · residual + intercept`.                                                                                                               |
| `fit_samples`                   | Number of overlap quarters (15-min entries) included in the regression.                                                                                                       |
| `fit_used_default`              | `true` if overlap < 24 quarters (= 6h) and fallback default coefficients were used.                                                                                           |
| `consumption_extended_quarters` | Number of 15-min quarters where the consumption forecast was extrapolated from last week's actuals (0 if not needed).                                                         |
| `wind_extended_quarters`        | Number of 15-min quarters where the wind power forecast was extrapolated from last week's actuals (0 if not needed).                                                          |
| `filled_quarters`               | Number of quarters that were forward-filled from the most recent predicted value (data thinning, not a hard outage). Normally 0.                                              |
| `zero_seeded_quarters`          | Number of quarters that fell back to `0.0` because neither actual nor predicted data was available (hard outage). If > 0, check Fingrid connectivity and the source sensor.   |
| `generated_at`                  | UTC timestamp marking when the forecast was computed.                                                                                                                         |

## Technical notes

### Native 15-min resolution

Nord Pool moved to 15-minute price periods (MTU = Market Time Unit) in 2025. The integration runs natively at 15-min resolution — both the regression fit and the forecast output operate in 15-min steps. The `forecast` attribute always contains **384 entries spanning 4 full days**.

For ApexCharts, the correct visualization for 15-min prices is **stepline** (a step function), not a smooth line: each quarter holds a flat price for its full duration, with sharp transitions between quarters. See the example below.

### 4-day series in whole local days

The series always covers **local midnight today + 4 days** = 384 quarters, regardless of wall-clock time. This gives an ApexCharts view (`graph_span: 4d`) a clean, day-aligned chart with no empty edges.

Fingrid's own horizons are shorter: the wind power forecast (245) extends ~72h and the consumption forecast (165) ~24h. The remaining quarters are filled from **last week's actuals** at the same weekday/quarter pair:

- **Consumption** (dataset 124, hourly resolution expanded to 4 quarters/hour) → when Fingrid's consumption forecast ends.
- **Wind power** (dataset 75, 15 min) → when Fingrid's wind power forecast ends.

The Finnish electricity-consumption weekly cycle is strong, so consumption extrapolation is accurate. Wind power varies with weather, making the same hour one week ago a coarser proxy — in practice, the inaccuracy shows only in the last 6–24h tail, which is fine for automations.

Check `consumption_extended_quarters` and `wind_extended_quarters` to see how many quarters were extrapolated from each source. Early in the morning both can be 0; toward evening they grow at roughly the same rate.

## ApexCharts card

Requires the [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) card from HACS (Frontend category).

The example below covers all the essentials:

- **From local midnight today** + 3 days ahead (`span.start: day`, `graph_span: 4d`).
- **Color coding by price level**: green < 15 c/kWh, yellow 15–30, red ≥ 30.
- **Nord Pool vs. forecast** distinction via opacity (published prices at full brightness, forecast dimmer).
- **"Now" marker** as a ▼ glyph.

```yaml
type: custom:apexcharts-card
grid_options:
  columns: full
graph_span: 4d
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
    name: Nord Pool
    opacity: 1
    data_generator: |
      return entity.attributes.forecast
        .filter(p => p.source === 'nordpool')
        .map(p => [new Date(p.start).getTime(), p.price]);
  - entity: sensor.spotoracle_forecast
    yaxis_id: price
    type: column
    name: Forecast
    opacity: 0.3
    data_generator: |
      return entity.attributes.forecast
        .filter(p => p.source === 'predicted')
        .map(p => [new Date(p.start).getTime(), p.price]);
```

The card shows **today's early morning → 3 days ahead** as color-coded bars. Green = cheap, yellow = mid-range, red = expensive. **Published Nord Pool prices** are rendered at full brightness, **the heuristic forecast** is dimmer (opacity 0.3). The yellow ▼ marks the current moment on the axis.

> Colors are defined via ApexCharts' native `plotOptions.bar.colors.ranges`, not the apexcharts-card wrapper's `color_threshold` — this is the most reliable way to get distinct per-bar colors without bleeding into a gradient at 15-min resolution.

## How the forecast is computed

1. Read published day-ahead prices from the source sensor's `prices` attribute (15-min entries).
2. Fetch four datasets from Fingrid Open Data in a single `/api/data` call:
   - **245** — wind power forecast (15 min, ~72h).
   - **75** — actual wind power (15 min, used to extrapolate the forecast).
   - **165** — consumption forecast (15 min, ~24h).
   - **124** — actual consumption (hourly, expanded to 4 quarters/hour).
3. Bucket into 15-min quarters → compute `residual = consumption − wind` per quarter.
4. For quarters with **both a published price and a Fingrid forecast**, fit a linear regression `price = a · residual + b`.
5. When Fingrid's own forecasts end, **extrapolate both consumption and wind power from last week's actuals** (same weekday + same quarter).
6. Apply the coefficients to all quarters where day-ahead has not been published yet.
7. Merge published + predicted quarters into a single 4 × 96 = 384-point series starting at local midnight, with no gaps and no null prices.

### Update frequency

- **Polling** Fingrid every 30 minutes. This is the upper bound on daily requests: ~144 requests/day per dataset, but all four are fetched in a single HTTP call → about 48 calls/day (the limit is 10,000).
- **Reactive refresh**: the integration listens for source-sensor state changes and refreshes itself **immediately** when the source's `prices` attribute changes. So when Nord Pool publishes tomorrow's prices around 14:00–15:00 EET and your source sensor picks them up, SpotOracle gets them within seconds — no waiting for the next 30-min cycle.

## HACS icon visibility

The `icon.png` at the root of this repo is **not picked up automatically** by HACS in the integration listing — HACS reads icons from the official [`home-assistant/brands`](https://github.com/home-assistant/brands) repository. To get the icon shown in the HACS listing, open a pull request against Brands that adds:

- `custom_integrations/spotoracle/icon.png` (256×256, this repo's `icon.png`).
- `custom_integrations/spotoracle/icon@2x.png` (512×512, this repo's `icon@2x.png`).

Instructions: <https://github.com/home-assistant/brands/blob/master/README.md>. Approval usually takes a few days. While waiting, HACS shows the default icon, but the integration works normally otherwise.

## License

MIT.

# SpotOracle (FI)

Home Assistant -integraatio, joka tuottaa **0–72h sähkönhintaennusteen** Suomen FI-hinta-alueelle yhdistämällä olemassa olevan Nord Pool day-ahead -hintasensorisi ja Fingrid Avoindata -ennusteet (tuulivoima + kulutus). Heuristinen lineaarinen regressiomalli — ei numpy/pandas/ML-riippuvuuksia.

## Mitä saat

Yksi sensori `sensor.spotoracle_forecast`, jonka attribuutti `forecast` sisältää listan `{start, price, source}`. `source` on `day_ahead` (julkaistu Nord Pool -hinta) tai `predicted` (heuristinen ennuste). Sopii suoraan ApexCharts-kortin `data_generator`:lle.

## Lähde-hintasensorin vaatimukset

Tarvitset olemassa olevan HA-sensorin, jonka attribuutti `prices` on lista:

```yaml
prices:
  - start: "2026-05-08T00:00:00+03:00"   # ISO8601 (paikallinen tai UTC)
    price: 4.21                           # snt/kWh
  - start: "2026-05-08T01:00:00+03:00"
    price: 3.87
```

Yhteensopivat lähteet (esim.):

- Nord Pool -tyyppiset HACS-integraatiot, jotka tuottavat `prices`-listan.
- Oma REST-sensori, joka hakee `api.spot-hinta.fi`:stä tai `elering.ee`:stä.
- Template-sensori, joka muuntaa toisen integraation hinnat tähän muotoon.

### Yksikkö

Ennusteen yksikkö periytyy lähdesensorista (`unit_of_measurement`-attribuutista). Eli jos sensorisi on `snt/kWh`, ennuste on `snt/kWh`. Jos käytät `c/kWh` tai `EUR/MWh`, ennuste tulee samassa yksikössä — kunhan attribuuteissa on yksikkö asetettuna.

### Fees ja siirtohinnat

Regressio sovittaa kertoimet suoraan lähdesensorin arvoja vasten, joten ennuste sisältää **automaattisesti samat fees:t** kuin lähdesensori. Jos sensorisi on jo "kokonaishinta" (sis. siirto, marginaali, verot), ennuste on kokonaishintaennuste. Jos sensorisi on puhdas spot, ennuste on puhdas spot.

**Rajoite**: jos fees-rakenteessa on selkeä aikariippuvuus (esim. yötariffi 22–07 / päivätariffi 07–22), lineaarinen regressio absorboi vain keskiarvon — yksittäiset yötunnit voivat olla 1–3 snt/kWh keskimääräisen biasin verran pielessä. Riittävä useimpiin automaatioihin; tunti-bias-korjaus on suunnitelmissa myöhempään versioon.

## Asennus (HACS)

1. Avaa **HACS** → ⋮ → **Custom repositories**.
2. Liitä URL `https://github.com/jonikanerva/spotoracle`, kategoria **Integration** → **Add**.
3. Etsi listalta **"SpotOracle"** → **Download** → käynnistä Home Assistant uudelleen.
4. **Settings → Devices & services → Add integration → "SpotOracle (FI)"**.
5. Hae [Fingrid Avoindata -API-avain](https://developer-data.fingrid.fi/apis) (ilmainen, vain sähköpostirekisteröinti) ja valitse lähdesensorisi dropdownista.

## Sensorin attribuutit

| Attribuutti | Merkitys |
|---|---|
| `forecast` | Lista `{start, price, source}` 0–72h. Käytä ApexChartsin `data_generator`:n syötteenä. |
| `source` | Nykytunnin lähde: `day_ahead` tai `predicted`. |
| `slope`, `intercept` | Regression kertoimet `price = slope · residual + intercept`. |
| `fit_samples` | Montako overlap-tuntia regressioon mukaan. |
| `fit_used_default` | `true` jos overlap < 6h ja palaudutaan oletuskertoimiin. |
| `consumption_extended_hours` | Montako tuntia kulutusennustetta ekstrapoloitiin viime viikon datalla (0 jos ei tarvittu). |
| `generated_at` | UTC-aikaleima ennusteen tuottohetkestä. |

## Tekniset huomiot

### 15 min spot-hinnat

Nord Pool siirtyi 15 minuutin hinta-aikajaksoihin (MTU) vuonna 2025. Lähde-hintasensorisi `prices`-attribuutti voi sisältää joko tunneittaisia tai 15 minuutin entryjä. Integraatio aggregoi 15 min hinnat tunneiksi keskiarvona ennen regression sovitusta — eli oikea käsittely riippumatta lähteen resoluutiosta. Ennusteen oma resoluutio on tunneittainen, mikä sopii ApexChartsin tuntipalkkeihin.

### 72h horisontti

Fingridin tuulivoimaennuste (dataset 245) ulottuu 72h, mutta kulutusennuste (165) vain ~24h. Jotta saadaan täydet 72h, integraatio ekstrapoloi puuttuvat kulutustunnit **viime viikon TOTEUTUNEILLA arvoilla** samalta viikonpäivä-tunti-parilta (dataset 124). Suomen sähkönkulutus seuraa selkeää viikkorytmiä, joten tämä on käytännössä riittävän tarkka.

Tarkista `consumption_extended_hours`-attribuutti nähdäksesi montako tuntia ekstrapoloitiin. Kun arvo on 0, koko sarja on Fingridin oman ennusteen pohjalta.

## ApexCharts-kortti

Vaatii [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) -kortin asennuksen HACSista (Frontend-kategoria).

```yaml
type: custom:apexcharts-card
graph_span: 72h
span:
  start: hour
header:
  show: true
  title: Sähkönhinta 0–72h (FI)
  show_states: true
yaxis:
  - id: price
    decimals: 2
    apex_config:
      title:
        text: snt/kWh
series:
  - entity: sensor.spotoracle_forecast
    name: Hinta
    yaxis_id: price
    type: line
    stroke_width: 2
    data_generator: |
      return entity.attributes.forecast.map(p => [
        new Date(p.start).getTime(),
        p.price
      ]);
  - entity: sensor.spotoracle_forecast
    name: Day-ahead (julkaistu)
    yaxis_id: price
    type: column
    opacity: 0.3
    data_generator: |
      return entity.attributes.forecast
        .filter(p => p.source === 'day_ahead')
        .map(p => [new Date(p.start).getTime(), p.price]);
```

Linechart-viiva ulottuu yli koko 72h, ja day-ahead -tunnit korostuvat himmeinä pylväinä alla — näet selvästi missä julkaistu hinta päättyy ja heuristiikka alkaa.

## Miten ennuste lasketaan

1. Lue lähdesensorin attribuutista `prices` julkaistut day-ahead -tunnit.
2. Hae Fingrid Avoindatasta:
   - Dataset **245** = tuulivoiman tuotantoennuste (15 min, ~72h)
   - Dataset **165** = sähkönkulutusennuste seuraavalle vuorokaudelle
3. Aggregoi tunneiksi → laske `residual = consumption − wind` per tunti.
4. Niiltä tunneilta, joille on sekä julkaistu hinta että Fingrid-ennuste, sovita lineaarinen regressio `price = a · residual + b`.
5. Sovella kertoimia tunteihin, joille day-aheadia ei vielä ole julkaistu.
6. Yhdistä julkaistut + ennustetut tunnit yhdeksi 72h sarjaksi.

Päivitysväli on 30 min, eli noin 96 Fingrid-pyyntöä/vrk (raja on 10 000/vrk).

## Lisenssi

MIT.

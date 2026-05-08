# SpotOracle (FI)

Home Assistant -integraatio, joka tuottaa **0–72h sähkönhintaennusteen** Suomen FI-hinta-alueelle yhdistämällä olemassa olevan Nord Pool day-ahead -hintasensorisi ja Fingrid Avoindata -ennusteet (tuulivoima + kulutus). Heuristinen lineaarinen regressiomalli — ei numpy/pandas/ML-riippuvuuksia.

## Mitä saat

Yksi sensori `sensor.spotoracle_forecast`, jonka attribuutti `forecast` sisältää listan `{start, price, source}` **15 minuutin resoluutiolla** (288 entryä per 72h). `source` on `day_ahead` (julkaistu Nord Pool -hinta) tai `predicted` (heuristinen ennuste). Sopii suoraan ApexCharts-kortin `data_generator`:lle.

## Lähde-hintasensorin vaatimukset

Tarvitset olemassa olevan HA-sensorin, jonka attribuutti `prices` on lista 15 min entryjä:

```yaml
prices:
  - start: "2026-05-08T00:00:00+03:00"   # ISO8601 (paikallinen tai UTC), 15 min stepit
    price: 4.21                           # snt/kWh
  - start: "2026-05-08T00:15:00+03:00"
    price: 4.05
  - start: "2026-05-08T00:30:00+03:00"
    price: 3.92
  - start: "2026-05-08T00:45:00+03:00"
    price: 3.87
```

> Nord Pool siirtyi 15 minuutin MTU-hinnoitteluun (Market Time Unit) vuonna 2025; käytä lähdesensoria, joka tarjoaa 15 min hintaentryjä.

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
| `forecast` | Lista `{start, price, source}` 0–72h **15 min resoluutiolla** (288 entryä). Käytä ApexChartsin `data_generator`:n syötteenä. |
| `source` | Nykyhetken (15 min) lähde: `day_ahead` tai `predicted`. |
| `slope`, `intercept` | Regression kertoimet `price = slope · residual + intercept`. |
| `fit_samples` | Montako overlap-quarteria (15 min entryä) regressioon mukaan. |
| `fit_used_default` | `true` jos overlap < 24 quarteria (= 6h) ja palaudutaan oletuskertoimiin. |
| `consumption_extended_quarters` | Montako 15 min quarteria kulutusennustetta ekstrapoloitiin viime viikon datalla (0 jos ei tarvittu). |
| `generated_at` | UTC-aikaleima ennusteen tuottohetkestä. |

## Tekniset huomiot

### Natiivi 15 min resoluutio

Nord Pool siirtyi 15 minuutin hinta-aikajaksoihin (MTU = Market Time Unit) vuonna 2025. Integraatio toimii natiivisti 15 min resoluutiolla — sekä regression sovitus että ennusteen tuotanto tapahtuvat 15 min stepeillä. `forecast`-attribuutti sisältää 288 entryä per 72h.

ApexChartsin oikea visualisointi 15 min hinnoille on **stepline** (askelfunktio), ei sileä viiva: jokainen quarter on tasahinta koko jaksonsa ajan, ja hinta vaihtuu jyrkästi quarterien välillä. Ks. esimerkki alla.

### 72h horisontti

Fingridin tuulivoimaennuste (dataset 245) ulottuu 72h 15 min resoluutiolla. Kulutusennuste (165) ulottuu vain ~24h. Jotta saadaan täydet 72h, integraatio ekstrapoloi puuttuvat kulutusquarterit **viime viikon TOTEUTUNEILLA arvoilla** samalta viikonpäivä-quarter-parilta (dataset 124).

Dataset 124 on tunti-resoluutiolla, joten kukin tuntiarvo levitetään neljään saman tunnin quarteriin (sama arvo). Suomen sähkönkulutus seuraa selkeää viikkorytmiä, joten approksimaatio on käytännössä riittävän tarkka automaatioiden ohjaukseen.

Tarkista `consumption_extended_quarters`-attribuutti nähdäksesi montako 15 min steppiä ekstrapoloitiin. Kun arvo on 0, koko sarja on Fingridin oman ennusteen pohjalta.

## ApexCharts-kortti

Vaatii [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) -kortin asennuksen HACSista (Frontend-kategoria). Esimerkki käyttää **stepline**-käyrää, joka on oikea visualisointi 15 min MTU-hinnoille (askelmainen, kuten Sähkövatkaimessa):

```yaml
type: custom:apexcharts-card
graph_span: 72h
span:
  start: hour
header:
  show: true
  title: Sähkönhinta 0–72h (FI, 15 min)
  show_states: true
apex_config:
  legend:
    show: true
  tooltip:
    x:
      format: 'ddd HH:mm'
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
    curve: stepline
    stroke_width: 2
    color: '#1e88e5'
    data_generator: |
      return entity.attributes.forecast.map(p => [
        new Date(p.start).getTime(),
        p.price
      ]);
  - entity: sensor.spotoracle_forecast
    name: Day-ahead (julkaistu)
    yaxis_id: price
    type: area
    curve: stepline
    stroke_width: 0
    opacity: 0.18
    color: '#1e88e5'
    data_generator: |
      return entity.attributes.forecast
        .filter(p => p.source === 'day_ahead')
        .map(p => [new Date(p.start).getTime(), p.price]);
```

Stepline-kaavion sininen viiva ulottuu yli koko 72h ja taustalla oleva pehmeä alue korostaa, missä julkaistut Nord Pool -hinnat päättyvät ja heuristinen ennuste alkaa.

## Miten ennuste lasketaan

1. Lue lähdesensorin attribuutista `prices` julkaistut day-ahead -hinnat (15 min entryt).
2. Hae Fingrid Avoindatasta:
   - Dataset **245** = tuulivoiman tuotantoennuste (15 min, ~72h)
   - Dataset **165** = sähkönkulutusennuste seuraavalle vuorokaudelle (15 min)
   - Dataset **124** = toteutunut kulutus (tunti-resoluutio, levitetään 4 quarteriin per tunti)
3. Bucket 15 min quartereihin → laske `residual = kulutus − tuulituotanto` per quarter.
4. Quartereille, joille on sekä julkaistu hinta että Fingrid-ennuste, sovita lineaarinen regressio `price = a · residual + b`.
5. Sovella kertoimia quartereihin, joille day-aheadia ei vielä ole julkaistu.
6. Kun Fingridin oma kulutusennuste loppuu (~24h), ekstrapoloidaan loppuhorisontti viime viikon toteutuneesta kulutuksesta (sama viikonpäivä, sama quarter).
7. Yhdistä julkaistut + ennustetut quarterit yhdeksi 288-pisteen sarjaksi.

Päivitysväli on 30 min, eli noin 96 Fingrid-pyyntöä/vrk (raja on 10 000/vrk).

## Lisenssi

MIT.

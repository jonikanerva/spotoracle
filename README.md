# SpotOracle (FI)

Home Assistant -integraatio, joka tuottaa **0–72h sähkönhintaennusteen** Suomen FI-hinta-alueelle yhdistämällä olemassa olevan Nord Pool day-ahead -hintasensorisi ja Fingrid Avoindata -ennusteet (tuulivoima + kulutus). Heuristinen lineaarinen regressiomalli — ei numpy/pandas/ML-riippuvuuksia.

## Mitä saat

Yksi sensori `sensor.spotoracle_forecast`, jonka attribuutti `forecast` sisältää listan `{start, price, source}` **15 minuutin resoluutiolla** kuluvan päivän alusta + 4 päivää eteenpäin (= 384 entryä, joista loppuosa on ennustetta kun day-ahead-hinnat eivät vielä ole julkaistu). `source` on `nordpool` (julkaistu hinta lähdesensorista) tai `predicted` (heuristinen ennuste). Sopii suoraan ApexCharts-kortin `data_generator`:lle.

## Lähde-hintasensorin vaatimukset

Tarvitset olemassa olevan HA-sensorin, jonka attribuutti `prices` on lista 15 min entryjä:

```yaml
prices:
  - start: "2026-05-08T00:00:00+03:00" # ISO8601 (paikallinen tai UTC), 15 min stepit
    price: 4.21 # snt/kWh
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

| Attribuutti                     | Merkitys                                                                                                                                                                                                               |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `forecast`                      | Lista `{start, price, source}` **15 min resoluutiolla** kuluvan päivän alusta (paikallisaika 00:00) → +4 päivää = 384 entryä. Käytä ApexChartsin `data_generator`:n syötteenä. |
| `source`                        | Nykyhetken (15 min) lähde: `nordpool` tai `predicted`.                                                                                                                        |
| `slope`, `intercept`            | Regression kertoimet `price = slope · residual + intercept`.                                                                                                                  |
| `fit_samples`                   | Montako overlap-quarteria (15 min entryä) regressioon mukaan.                                                                                                                 |
| `fit_used_default`              | `true` jos overlap < 24 quarteria (= 6h) ja palaudutaan oletuskertoimiin.                                                                                                     |
| `consumption_extended_quarters` | Montako 15 min quarteria kulutusennustetta ekstrapoloitiin viime viikon toteutuneella datalla (0 jos ei tarvittu).                                                            |
| `wind_extended_quarters`        | Montako 15 min quarteria tuulivoimaennustetta ekstrapoloitiin viime viikon toteutuneella datalla (0 jos ei tarvittu).                                                         |
| `generated_at`                  | UTC-aikaleima ennusteen tuottohetkestä.                                                                                                                                       |

## Tekniset huomiot

### Natiivi 15 min resoluutio

Nord Pool siirtyi 15 minuutin hinta-aikajaksoihin (MTU = Market Time Unit) vuonna 2025. Integraatio toimii natiivisti 15 min resoluutiolla — sekä regression sovitus että ennusteen tuotanto tapahtuvat 15 min stepeillä. `forecast`-attribuutti sisältää 288 entryä per 72h.

ApexChartsin oikea visualisointi 15 min hinnoille on **stepline** (askelfunktio), ei sileä viiva: jokainen quarter on tasahinta koko jaksonsa ajan, ja hinta vaihtuu jyrkästi quarterien välillä. Ks. esimerkki alla.

### 4 päivän sarja kokonaisina vuorokausina

Sarja kattaa aina **kuluvan päivän alusta + 4 päivää** = 384 quarteria, riippumatta kellonajasta. Tämä antaa ApexCharts-näkymälle (`graph_span: 4d`) tasaisen, päivän rajoihin loksahtavan kuvaajan ilman tyhjiä päitä.

Fingridin omat horisontit ovat lyhyemmät: tuulivoimaennuste (245) ulottuu ~72h ja kulutusennuste (165) ~24h. Loput sarjasta täytetään **viime viikon toteutuneella datalla** samalta viikonpäivä-quarter-parilta:

- **Kulutus** (dataset 124, tunti-resoluutio levitettynä 4 quarteriin/tunti) → kun Fingridin kulutusennuste loppuu
- **Tuulivoima** (dataset 75, 15 min) → kun Fingridin tuulivoimaennuste loppuu

Suomen sähkönkulutuksen viikkorytmi on vahva, joten kulutusekstrapolointi on tarkka. Tuulivoima vaihtelee säätilan mukaan, joten viikon takainen sama hetki on karkeampi proxy — käytännössä epätarkkuus näkyy vain viimeisten 6–24h pyrstössä, joka on automaatioiden kannalta riittävä.

Tarkista `consumption_extended_quarters` ja `wind_extended_quarters` -attribuutit nähdäksesi montako quarteria kummaltakin lähteeltä ekstrapoloitiin. Aikaisin aamulla kummatkin voivat olla 0; iltaa kohti ne kasvavat samaa tahtia.

## ApexCharts-kortti

Vaatii [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) -kortin asennuksen HACSista (Frontend-kategoria).

Esimerkki kattaa kaikki olennaiset ominaisuudet:

- **Kuluvan päivän alusta** + 3 päivää eteenpäin (`span.start: day`, `graph_span: 4d`)
- **Värikoodaus hintatason mukaan**: vihreä < 15 snt/kWh, keltainen 15–30, punainen ≥ 30
- **Nordpool vs. ennuste** -erottelu opacity:n kautta (julkaistut hinnat täydellä kirkkaudella, ennuste haaleammin)
- **"Nyt"-merkki** ▼-glyfinä

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
    name: Nordpool
    opacity: 1
    data_generator: |
      return entity.attributes.forecast
        .filter(p => p.source === 'nordpool')
        .map(p => [new Date(p.start).getTime(), p.price]);
  - entity: sensor.spotoracle_forecast
    yaxis_id: price
    type: column
    name: Ennuste
    opacity: 0.3
    data_generator: |
      return entity.attributes.forecast
        .filter(p => p.source === 'predicted')
        .map(p => [new Date(p.start).getTime(), p.price]);
```

Kortti näyttää **kuluvan päivän aamuyön → 3 päivää eteenpäin** värikoodattuina palkkeina. Vihreä = halpa, keltainen = keskitaso, punainen = kallis. **Julkaistut Nord Pool -hinnat** näkyvät täydellä kirkkaudella, **heuristinen ennuste** haaleammin (opacity 0.3). Keltainen ▼-merkki osoittaa nykyhetken paikan akselilla.

> Värit määritellään ApexCharts:n natiivilla `plotOptions.bar.colors.ranges` -ominaisuudella, ei apexcharts-card -wrapperin `color_threshold`:lla — tämä on luotettavin tapa saada erilliset värit per palkki, eikä se sulaudu gradientiksi 15 min datalla.

## Miten ennuste lasketaan

1. Lue lähdesensorin attribuutista `prices` julkaistut day-ahead -hinnat (15 min entryt).
2. Hae Fingrid Avoindatasta yhdellä `/api/data` -kutsulla neljä datasettiä:
   - **245** — tuulivoimaennuste (15 min, ~72h)
   - **75** — toteutunut tuulivoima (15 min, käytetään ennusteen ekstrapolointiin)
   - **165** — kulutusennuste (15 min, ~24h)
   - **124** — toteutunut kulutus (tunti-resoluutio, levitetään 4 quarteriin/tunti)
3. Bucket 15 min quartereihin → laske `residual = kulutus − tuulituotanto` per quarter.
4. Quartereille joille on **sekä julkaistu hinta että Fingrid-ennuste**, sovita lineaarinen regressio `price = a · residual + b`.
5. Kun Fingridin omat ennusteet loppuvat, **ekstrapoloi sekä kulutus että tuulivoima viime viikon toteutuneesta datasta** (sama viikonpäivä + sama quarter).
6. Sovella kertoimia kaikkiin quartereihin joille day-aheadia ei vielä ole julkaistu.
7. Yhdistä julkaistut + ennustetut quarterit yhdeksi 4 × 96 = 384 pisteen sarjaksi, joka alkaa local-midnightistä.

### Päivitystiheys

- **Pollaus** Fingridille 30 min välein. Tämä on päiväpyyntöjen yläraja: ~144 pyyntöä/vrk per dataset, mutta kaikki haetaan yhdellä HTTP-kutsulla → noin 48 kutsua/vrk (raja 10 000).
- **Reaktiivinen päivitys**: integraatio kuuntelee lähdesensorin tilan vaihdoksia ja päivittää itsensä **välittömästi** kun lähdesensorin `prices`-attribuutti muuttuu. Tämä tarkoittaa että kun Nord Pool julkaisee huomisen hinnat klo 14:00–15:00 EET ja lähdesensorisi tartuu niihin, SpotOracle saa ne sekunnissa — ei tarvitse odottaa seuraavaa 30 min sykliä.

## Lisenssi

MIT.

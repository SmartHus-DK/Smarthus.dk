<p align="center"><img src="logo.png" alt="SmartHus.dk" width="220"></p>

# SmartHus for Home Assistant

**Version 1.10.0** – kortet viser sin version nederst. Står der et lavere tal på kortet, så genstart Home Assistant og nulstil app-cachen.

Intelligent energistyring efter elprisen – fra [SmartHus.dk](https://smarthus.dk).

SmartHus henter dagens og morgendagens spotpriser fra Energi Data Service, lægger tariffer, afgifter og moms oveni, og styrer dine enheder, så strømmen bruges når den er billigst og sælges eller bruges fra batteriet når den er dyrest.

| Modul | Hvad det gør |
|---|---|
| 🔋 **Batteri / inverter** | Lader fra nettet i de billigste timer og aflader i de dyreste – kun hvis prisforskellen er stor nok. Respekterer min./maks. batteriniveau. Lader også når salgsprisen er negativ. |
| ☀️ **Solceller** | Begrænser eksport når salgsprisen er negativ og giver et overblik over produktion, hvad solcellerne har sparet dig, og hvad du har tjent ved salg – i dag, pr. måned, pr. år og i alt. |
| 🚗 **Elbillader** | Lader bilen op til din mål-SoC (fx 80 %) i de billigste timer inden dit "klar kl."-tidspunkt og stopper, når målet er nået. Knap til "Lad elbil nu". |
| 🚿 **Varmt vand** | Opvarmer i dagens billigste sammenhængende periode (kontakt eller temperatur). |
| 🔥 **Varme / varmepumpe** | Hæver temperaturen X grader i de N billigste timer og sænker den i de dyreste. |
| 💰 **Besparelser** | Viser hvad SmartHus-styringen har sparet i dag, denne måned og i alt – fordelt på elbil, batteri, varme og varmt vand. Solcellerne vises for sig. |

SmartHus styrer via **dine eksisterende entiteter**, så det virker med SigEnergy og de fleste andre invertere, ladebokse og varmepumper der allerede er i Home Assistant.

---

Kræver Home Assistant 2025.1 eller nyere. SmartHus-logoet vises på integrationssiden fra Home Assistant 2026.3.

## Installation via HACS

1. Åbn **HACS** i Home Assistant.
2. Klik **⋮ → Brugerdefinerede repositories** (Custom repositories).
3. Indsæt `https://github.com/SmartHus-DK/Smarthus.dk`, vælg type **Integration**, og klik **Tilføj**.
4. Søg efter **SmartHus**, klik **Download**, og genstart Home Assistant.
5. Gå til **Indstillinger → Enheder & tjenester → Tilføj integration → SmartHus**.

### Manuel installation
Kopiér mappen `custom_components/smarthus` til `config/custom_components/` og genstart.

---

## Opsætning

**Trin 1 – Elpris:** Vælg prisområde (DK1 vest / DK2 øst) og indtast tillæg og nettarif i kr/kWh ekskl. moms. Værdierne står på din elregning eller på dit netselskabs hjemmeside. Standardværdierne er kun et udgangspunkt.

**Trin 2 – Enheder:** Klik **Konfigurer** på SmartHus-integrationen og vælg de moduler du vil bruge. Alle felter er valgfrie.

### Elpris fra Strømligning (anbefalet)
Har kunden [Strømligning-integrationen](https://github.com/MTrab/stromligning) installeret (HACS), kan SmartHus bruge kundens **rigtige pris fra elabonnementet** – inkl. leverandørens tillæg, nettarif, afgifter og moms. Så skal der ikke tastes tariffer ind.

1. Installér Strømligning via HACS, og vælg kundens elselskab og produkt i den.
2. Gå til SmartHus → **Konfigurer → Elpris og tariffer**, og vælg **Strømligning**.
3. Prissensoren er som standard `sensor.stromligning_current_price_vat`, og morgendagens priser hentes fra `binary_sensor.stromligning_tomorrow_available_vat`.

Salgsprisen for solceller beregnes ud fra spotprisen fra `sensor.stromligning_spotprice_vat` (uden moms, minus fradraget ved salg). Energi Data Service bruges som reserve. Mangler Strømligning priser for et tidsrum, bruger SmartHus sin egen beregning som reserve.

### SigEnergy-eksempel
Med en SigEnergy-integration (Modbus) vælger du under **Batteri / inverter**:

| Felt | Eksempel |
|---|---|
| Entitet for driftstilstand | `select.sigen_plant_remote_ems_control_mode` |
| Tilstand: lad fra nettet | `Command Charging (Grid First)` |
| Tilstand: aflad batteri | `Command Discharging (ESS First)` |
| Tilstand: normal drift | `Maximum Self Consumption` |
| Batteriniveau | `sensor.sigen_plant_battery_state_of_charge` |

Navnene skal stå **præcis** som i inverterens rullemenu (se entitetens mulige værdier under Udviklerværktøjer → Tilstande). Husk at aktivere "Remote EMS" på anlægget, hvis din integration kræver det.

### Andre invertere og batterier – rullemenu eller scripts
SmartHus kan styre batteriet på to måder (under **Konfigurer → Batteri / inverter**):

- **A) Rullemenu** – når inverteren har én `select` med tilstande (SigEnergy, ofte FoxESS og SolaX). Skriv navnene præcis som i rullemenuen.
- **B) Scripts** – virker med **alle** invertere, der har en Home Assistant-integration. Lav tre scripts (lad fra nettet, aflad, normal drift) under **Indstillinger → Automatiseringer → Scripts**, og vælg dem i opsætningen. Er der valgt et script for en tilstand, bruges det i stedet for rullemenuen.

SmartHus kører kun et script, når tilstanden **skifter**, og "normal drift" køres også, når styringen slås fra. Scriptet får disse variabler: `smarthus_state` (`charge`, `discharge` eller `normal`), `price`, `sale_price`, `min_soc` og `max_soc`.

**Eksport** kan stoppes med en kontakt, en rullemenu (to navne), et tal som fx en eksportgrænse i W (to værdier) eller to scripts (stop/start) – under **Konfigurer → Solceller**.

| Mærke (integration) | Batteri | Stop af eksport |
|---|---|---|
| SigEnergy (Modbus) | A) rullemenu – se eksemplet ovenfor | kontakt eller tal |
| Huawei ([huawei_solar](https://github.com/wlcrs/huawei_solar)) | B) scripts med `forcible_charge_soc` / `forcible_discharge_soc` / `stop_forcible_charge` | tal (eksportgrænse) eller rullemenu |
| Deye / Sunsynk ([Solarman](https://github.com/davidrapan/ha-solarman)) | B) scripts der sætter tidsprogram, netopladning og SoC | rullemenu ("Work Mode") eller kontakt |
| Growatt (Modbus) | B) scripts der skriver Battery First / AC charge | script |
| Solplanet ([solplanet](https://github.com/calvinbui/home-assistant-solplanet)) | B) scripts med arbejdstilstand og tidsplan | script |
| FoxESS / SolaX (Modbus) | A) rullemenu, hvis integrationen har tilstande for tvungen opladning – ellers B) | tal eller rullemenu |

> Entiteternes navne varierer med integration, version og model. Brug eksemplerne som skabelon og find dine egne navne under **Udviklerværktøjer → Tilstande** og **Udviklerværktøjer → Handlinger**. Afprøv hvert script manuelt, før du slår SmartHus-styringen til.

<details><summary><b>Huawei – scripts</b></summary>

`device_id` er inverterens/batteriets enhed – vælg den i script-editoren. Feltnavnene kan ses under Udviklerværktøjer → Handlinger.

```yaml
smarthus_lad:
  alias: SmartHus – lad batteri fra nettet
  sequence:
    - action: huawei_solar.forcible_charge_soc
      data:
        device_id: DIN_BATTERI_ENHED
        target_soc: "{{ max_soc | int(95) }}"
        power: 5000
smarthus_aflad:
  alias: SmartHus – aflad batteri
  sequence:
    - action: huawei_solar.forcible_discharge_soc
      data:
        device_id: DIN_BATTERI_ENHED
        target_soc: "{{ min_soc | int(15) }}"
        power: 5000
smarthus_normal:
  alias: SmartHus – normal drift
  sequence:
    - action: huawei_solar.stop_forcible_charge
      data:
        device_id: DIN_BATTERI_ENHED
```
</details>

<details><summary><b>Deye / Sunsynk (Solarman) – scripts</b></summary>

Deye styres med tidsprogrammer. Eksemplet bruger program 1 for hele døgnet; tilpas entitetsnavnene til din installation.

```yaml
smarthus_lad:
  alias: SmartHus – lad batteri fra nettet
  sequence:
    - action: switch.turn_on
      target: { entity_id: switch.inverter_program_1_charging }
    - action: number.set_value
      target: { entity_id: number.inverter_program_1_soc }
      data: { value: "{{ max_soc | int(95) }}" }
smarthus_aflad:
  alias: SmartHus – aflad batteri
  sequence:
    - action: switch.turn_off
      target: { entity_id: switch.inverter_program_1_charging }
    - action: select.select_option
      target: { entity_id: select.inverter_work_mode }
      data: { option: Selling First }
    - action: number.set_value
      target: { entity_id: number.inverter_program_1_soc }
      data: { value: "{{ min_soc | int(15) }}" }
smarthus_normal:
  alias: SmartHus – normal drift
  sequence:
    - action: switch.turn_off
      target: { entity_id: switch.inverter_program_1_charging }
    - action: select.select_option
      target: { entity_id: select.inverter_work_mode }
      data: { option: Zero Export To CT }
    - action: number.set_value
      target: { entity_id: number.inverter_program_1_soc }
      data: { value: "{{ min_soc | int(15) }}" }
```
Stop af eksport på Deye: vælg `select.inverter_work_mode` som rullemenu med "Zero Export To CT" (stop) og "Selling First" (normal), eller kontakten "Export surplus".
</details>

<details><summary><b>Growatt (Modbus) – scripts</b></summary>

Eksempel med Home Assistants Modbus-integration (hub `growatt`, slave 1) på en SPH-model: Battery First-vindue (register 1100–1102) og AC-opladning (1090–1092). Tid skrives som time × 256 + minut, så 23:59 = 5947. Tjek registrene for din model.

```yaml
smarthus_lad:
  alias: SmartHus – lad batteri fra nettet
  sequence:
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1100, value: [0, 5947, 1] }
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1090, value: [0, 5947, 1] }
smarthus_aflad:
  alias: SmartHus – aflad batteri (Grid First)
  sequence:
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1100, value: [0, 0, 0] }
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1080, value: [0, 5947, 1] }
smarthus_normal:
  alias: SmartHus – normal drift (Load First)
  sequence:
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1100, value: [0, 0, 0] }
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1090, value: [0, 0, 0] }
    - action: modbus.write_register
      data: { hub: growatt, slave: 1, address: 1080, value: [0, 0, 0] }
```
</details>

<details><summary><b>Solplanet – scripts</b></summary>

Solplanet-integrationen styrer batteriet med en arbejdstilstand og tidsplaner. Brug **Self-consumption** til normal drift, og lad lad/aflad-scriptet skifte til **Custom** og lægge et tidsrum ind med handlingen *Solplanet: Set Schedule Slots* (opladning eller afladning). Felterne til handlingen ses under Udviklerværktøjer → Handlinger.

```yaml
smarthus_normal:
  alias: SmartHus – normal drift
  sequence:
    - action: select.select_option
      target: { entity_id: select.solplanet_battery_work_mode }
      data: { option: Self-consumption }
```
</details>

### Batteriets reserve (lærer husets forbrug)
Vælg **Energimåler: husets forbrug** under *Batteri / inverter* (valgfri) og indstil **Batteriets størrelse** i kortet. Så gør SmartHus følgende:

1. Hver nat lærer den husets forbrug time for time ud fra de seneste **7 dage** (hverdage og weekend for sig). Historikken hentes fra Home Assistants langtidsstatistik, så den virker med det samme, hvis måleren allerede har data. Elbilens forbrug trækkes fra, hvis bilen har en energimåler.
2. Den beregner, hvor meget huset forventes at bruge fra nu og til strømmen bliver billig igen (næste planlagte opladning) – minus forventet sol, hvis du har valgt en solprognose under *Solceller*.
3. I de dyreste timer sælges kun det, batteriet har **ud over reserven og laveste niveau**. Når reserven er nået, skifter batteriet til normal drift, så resten bruges i huset.

| Hvad | Forslag |
|---|---|
| Husets forbrug | En stigende kWh-måler for husets forbrug fra inverteren, en P1-måler eller en elmåler. "Import fra nettet" kan bruges, men så tæller opladning fra nettet med, og reserven bliver lidt for stor. |
| Solprognose | Solcast: `sensor.solcast_pv_forecast_forecast_remaining_today` · Forecast.Solar: `sensor.energy_production_today_remaining`. Uden prognose regnes der forsigtigt uden sol. |
| Batteriniveau | Kræves for at regne ud, hvor meget der må sælges. |

Reserven bruges, når der er mindst 2 hele døgn med historik. Se `sensor.smarthus_battery_reserve` (med det lærte forbrug som attributter) og `sensor.smarthus_battery_sellable`.

### Styring slås til
Når opsætningen er gemt, får du kontakter som `Styr batteri`, `Styr elbillader` osv. De er **slået fra som standard** – så sker der intet, før du selv tænder dem. Planen kan ses i de binære sensorer, før du slår styringen til.

Slår du et modul fra, sættes enheden tilbage til normal drift (batteri i normal tilstand, laderen tændt, grundtemperatur).

---

### Flere elbiler
Under **Konfigurer → Elbiler** kan du tilføje så mange biler, du vil. Hver bil får sit eget navn, sin egen lader, SoC-sensor og energimåler. Den får også sine egne indstillinger (lad til, klar kl., batteristørrelse og ladeeffekt), sin egen plan, sin egen "Lad nu"-knap og sin egen sammenligning af "lad nu" og "billigste timer". Her kan du også rette eller fjerne en bil.

**Maks. samlet ladeeffekt** (fx 11 kW) sørger for, at bilerne ikke lader samtidig ud over hvad hovedsikringen kan klare. Bilen med det tidligste klar-tidspunkt får de billigste timer først. 0 betyder ingen grænse (standard). Indstillingen vises, når der er mindst to biler.

Den første bil beholder de oprindelige entity-id'er (fx `switch.smarthus_control_ev`), så eksisterende automatiseringer virker uændret.

### Varmt vand med kontakt og temperaturføler
Styres varmt vand med en **kontakt**, tændes den i dagens billigste periode. Vælger du også en **temperaturføler i vandet**, slukker SmartHus kontakten, når vandet når **Maks. temperatur** (på kortet), og tænder igen i den billige periode, hvis vandet er kølet 3 °C af. Uden føler bestemmer varmelegemets egen termostat temperaturen, og der vises ingen temperaturindstillinger på kortet. "Normal temperatur" bruges kun ved en water_heater/climate-entitet.

### Solcelleoverskud (varmt vand og elbil)
Vælg under **Konfigurer → Solceller** en sensor for **effekt til/fra nettet lige nu** (W eller kW), og angiv om positiv betyder eksport eller køb. Vælg gerne også **solcelleproduktion lige nu**.

**Varmt vand:** Sæt **Overskud over** på kortet (fx 2 kW, 0 = fra). Når huset har sendt så meget ud på nettet i 2 minutter, tændes varmt vand. Der stoppes først, når huset har købt mere end 0,3 kW i 2 minutter. Med **Kun når det betaler sig** (standard) bruges overskuddet kun, hvis salgsprisen lige nu er lavere end prisen i den billige time senere – ellers sælges strømmen, og vandet varmes i den billige time. Der varmes **højst én gang om dagen**: når vandet er varmet med overskud (maks. temperatur nået eller timerne brugt), springes dagens billige time over, og er den billige time allerede kørt, bruges overskuddet ikke den dag.

**Elbil:** Vælg under **Konfigurer → Elbiler** hvordan ladestrømmen styres – en **tal-entitet** (A) eller et **script**, der får variablen `current`. Slå **Lad med solcelleoverskud** til på kortet. Hver 30. sekund justeres strømmen 1 A ad gangen: ned ved køb fra nettet (> 0,3 kW), op ved eksport (> 0,5 kW) når solen giver mindst **Start ved sol over** (fx 4 kW), ellers holdes den. Der startes på **Mindste ladestrøm** og lades aldrig over **Største ladestrøm**. Uden sol i 3 minutter stopper solcelleladningen. I billige timer, ved "Lad nu" og når eksporten er stoppet pga. lav salgspris, lades med største strøm. Kun én bil ad gangen lader med sol. Mål-SoC gælder stadig.

<details><summary><b>Easee – script til ladestrøm</b></summary>

```yaml
smarthus_easee_strom:
  alias: SmartHus – Easee ladestrøm
  mode: restart
  sequence:
    - action: easee.set_charger_dynamic_limit
      data:
        device_id: DIN_EASEE_ENHED
        current: "{{ current | int }}"
        time_to_live: 0
```
</details>

<details><summary><b>Zaptec og andre ladebokse</b></summary>

Har ladeboksens integration en **number**-entitet for ladestrøm (fx Zaptec "Available current"), så vælg den direkte som "Ladestrøm: tal-entitet". Ellers lav et script som for Easee med den handling, din integration har (se Udviklerværktøjer → Handlinger). Afprøv scriptet manuelt med fx `current: 6`.
</details>

### Stop eksport under en bestemt pris
Under **Solceller** på kortet kan du sætte **Stop eksport under** (kr/kWh). Når salgsprisen er under grænsen, tændes kontakten "begræns eksport", og batteriet lader af solstrømmen i stedet. Når prisen kommer over grænsen igen, starter eksporten automatisk. Standard er 0 kr, dvs. der stoppes kun ved negativ pris.

### Kortet i fuld skærm
Kortets bredde bestemmes af dashboard-visningen. Vil du have det til at fylde hele skærmen: **Rediger dashboard → tryk på blyanten ved fanen → Visningstype: Panel (1 kort)**.

### Tidsrum
Batteri, varmt vand og varme har hvert sit **tidsrum** (fra–til kl.), så kunden kan bestemme, hvornår SmartHus må handle:

- **Batteri:** fx kun lade fra nettet kl. 22–06 og kun aflade kl. 17–21.
- **Varmt vand:** fx kun opvarme kl. 00–07, så vandet er varmt om morgenen.
- **Varme:** fx kun hæve kl. 06–16 og kun sænke kl. 17–21.

Fra 0 til 24 betyder hele døgnet (standard). Et tidsrum som 22–06 går hen over midnat. SmartHus vælger stadig de billigste (eller dyreste) timer – men kun inden for tidsrummet.

## Entiteter

Alle entiteter har faste id'er, uanset sprog:

- `sensor.smarthus_current_price` – elpris nu (attributter `today`/`tomorrow` til grafer)
- `sensor.smarthus_status` – fx "Lader batteri · Lader elbil"
- `sensor.smarthus_savings_today` / `_month` / `_total` – SmartHus-besparelse i kr (uden solceller)
- `sensor.smarthus_battery_reserve` / `_battery_sellable` – reserve til huset og hvad der må sælges (kræver husets forbrug)
- `sensor.smarthus_solar_produced_*`, `_solar_saved_*`, `_solar_sold_*`, `_solar_value_*` – solcelleoverblik for `today`, `month`, `year` og `total`
- `binary_sensor.smarthus_ev_charge_now` m.fl. – hvad planen ønsker nu, med attributten `periods`
- `switch.smarthus_control_*` – slå styring til/fra pr. modul
- `number.smarthus_*` – antal timer, grader og temperaturer, kan ændres direkte fra dashboardet

## SmartHus-kortet
Integrationen har sit eget kort, som installeres automatisk – der skal ikke installeres noget ekstra.

1. Åbn et dashboard → **Rediger** → **Tilføj kort**.
2. Søg efter **SmartHus** og vælg kortet. Eller indsæt i YAML:

```yaml
type: custom:smarthus-card
```

Kortet viser elprisen nu, besparelser, en prisgraf for i dag og i morgen med et spor for hver enhed (hvornår elbil, batteri, varmt vand og varme kører), og styring af hvert modul med +/− knapper. For elbilen ser kunden, hvad det koster at lade **nu** sammenlignet med i de **billigste timer**, og hvor meget der spares.

Valgfrit: `title: Mit hus` ændrer overskriften. Har du flere SmartHus-opsætninger, kan `prefix` pege på en anden (standard `smarthus`).

Ser du ikke kortet efter en opdatering, så genindlæs siden (træk ned i appen eller tryk Ctrl+F5).

## Dashboard
Et komplet eksempel-dashboard med standardkort findes i `dashboards/smarthus-dashboard.yaml`. Prisgrafen bruger [ApexCharts Card](https://github.com/RomRider/apexcharts-card) fra HACS.

---

## Sådan beregnes det

- **Købspris** = (spot + leverandørtillæg + nettarif + Energinet-tarif + elafgift) × 1,25 moms
- **Salgspris** = spot − fradrag ved salg
- **Nettarif** følger Tarifmodel 3.0: lav 00–06, høj 06–17 og 21–24, spids 17–21. Vinter er oktober–marts.
- **Planer** laves pr. døgn. Morgendagens priser kommer ca. kl. 13, og så opdateres planen automatisk. Priserne er i 15-minutters opløsning.
- **Elbil med SoC:** vælg bilens batteriniveau-sensor i opsætningen og indstil "Elbil: lad til" (mål-SoC), batteristørrelse og ladeeffekt. Ladetiden beregnes som (mål − nu) × batteri ÷ ladeeffekt ÷ 0,9 (ladetab), og laderen slukkes, når mål-SoC er nået – også ved "Lad elbil nu". Planen genberegnes hvert minut, så den følger bilens faktiske niveau.
- **Elbil uden SoC:** lader et fast antal timer ("Elbil: timer at lade") og vælger de billigste tidsrum i de 24 timer op til "klar kl.". Hvis morgendagens priser endnu ikke er kendt, venter den – men lader altid i de sidste timer før klar-tidspunktet som sikkerhed.
- **Besparelse (elbil, varmepumpe, varmt vand)** = kWh × (dagens gennemsnitspris − prisen da strømmen blev brugt).
- **Elbil – lad nu vs. billigste timer:** sensoren `sensor.smarthus_ev_smart_saving` viser forskellen i kr. Attributterne `cost_now` og `cost_planned` er prisen for den samme mængde strøm (kWh) ladet med det samme eller i de planlagte timer.
- **Besparelse (batteri)** = afladet kWh × pris − opladet kWh × pris.
- **Solceller – sparet** = egetforbrug × købspris. **Solceller – tjent ved salg** = eksport × salgspris. Solcellerne tæller ikke med i SmartHus-besparelsen, fordi de også producerer uden styring.

Besparelser kræver, at du vælger energimålere (kWh, stigende) i opsætningen.

---

## Bemærk
- Når et modul er slået til, holder SmartHus enheden i den ønskede tilstand. Ændrer du manuelt termostaten, sætter SmartHus den tilbage inden for et par minutter. Slå modulet fra, hvis du vil styre manuelt.
- Beregnede besparelser er estimater.

Udviklet af SmartHus.dk · © SmartHus.dk – alle rettigheder forbeholdes. Må kun bruges af SmartHus-kunder, se [LICENSE](LICENSE).

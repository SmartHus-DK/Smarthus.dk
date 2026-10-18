<p align="center"><img src="logo.png" alt="SmartHus.dk" width="220"></p>

# SmartHus for Home Assistant

Intelligent energistyring efter elprisen – fra [SmartHus.dk](https://smarthus.dk).

SmartHus henter dagens og morgendagens spotpriser fra Energi Data Service, lægger tariffer, afgifter og moms oveni, og styrer dine enheder, så strømmen bruges når den er billigst og sælges eller bruges fra batteriet når den er dyrest.

| Modul | Hvad det gør |
|---|---|
| 🔋 **Batteri / inverter** | Lader fra nettet i de billigste timer og aflader i de dyreste – kun hvis prisforskellen er stor nok. Respekterer min./maks. batteriniveau. Lader også når salgsprisen er negativ. |
| ☀️ **Solceller** | Begrænser eksport når salgsprisen er negativ og beregner værdien af din solcellestrøm. |
| 🚗 **Elbillader** | Lader bilen op til din mål-SoC (fx 80 %) i de billigste timer inden dit "klar kl."-tidspunkt og stopper, når målet er nået. Knap til "Lad elbil nu". |
| 🚿 **Varmt vand** | Opvarmer i dagens billigste sammenhængende periode (kontakt eller temperatur). |
| 🔥 **Varme / varmepumpe** | Hæver temperaturen X grader i de N billigste timer og sænker den i de dyreste. |
| 💰 **Besparelser** | Viser besparelse i dag, denne måned og i alt – fordelt på batteri, solceller, elbil og varmepumpe. |

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

Salgsprisen for solceller beregnes stadig ud fra spotprisen fra Energi Data Service. Mangler Strømligning priser for et tidsrum, bruger SmartHus sin egen beregning som reserve.

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

### Styring slås til
Når opsætningen er gemt, får du kontakter som `Styr batteri`, `Styr elbillader` osv. De er **slået fra som standard** – så sker der intet, før du selv tænder dem. Planen kan ses i de binære sensorer, før du slår styringen til.

Slår du et modul fra, sættes enheden tilbage til normal drift (batteri i normal tilstand, laderen tændt, grundtemperatur).

---

### Flere elbiler
Under **Konfigurer → Elbiler** kan du tilføje så mange biler, du vil. Hver bil får sit eget navn, sin egen lader, SoC-sensor og energimåler. Den får også sine egne indstillinger (lad til, klar kl., batteristørrelse og ladeeffekt), sin egen plan, sin egen "Lad nu"-knap og sin egen sammenligning af "lad nu" og "billigste timer". Her kan du også rette eller fjerne en bil.

**Maks. samlet ladeeffekt** (fx 11 kW) sørger for, at bilerne ikke lader samtidig ud over hvad hovedsikringen kan klare. Bilen med det tidligste klar-tidspunkt får de billigste timer først. 0 betyder ingen grænse (standard). Indstillingen vises, når der er mindst to biler.

Den første bil beholder de oprindelige entity-id'er (fx `switch.smarthus_control_ev`), så eksisterende automatiseringer virker uændret.

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
- `sensor.smarthus_savings_today` / `_month` / `_total` – besparelser i kr
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
- **Besparelse (elbil, varmepumpe)** = kWh × (dagens gennemsnitspris − prisen da strømmen blev brugt).
- **Elbil – lad nu vs. billigste timer:** sensoren `sensor.smarthus_ev_smart_saving` viser forskellen i kr. Attributterne `cost_now` og `cost_planned` er prisen for den samme mængde strøm (kWh) ladet med det samme eller i de planlagte timer.
- **Besparelse (batteri)** = afladet kWh × pris − opladet kWh × pris.
- **Værdi af solceller** = egetforbrug × købspris + eksport × salgspris.

Besparelser kræver, at du vælger energimålere (kWh, stigende) i opsætningen.

---

## Bemærk
- Når et modul er slået til, holder SmartHus enheden i den ønskede tilstand. Ændrer du manuelt termostaten, sætter SmartHus den tilbage inden for et par minutter. Slå modulet fra, hvis du vil styre manuelt.
- Beregnede besparelser er estimater.

Udviklet af SmartHus.dk · MIT-licens

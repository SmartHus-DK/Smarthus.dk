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

## Entiteter

Alle entiteter har faste id'er, uanset sprog:

- `sensor.smarthus_current_price` – elpris nu (attributter `today`/`tomorrow` til grafer)
- `sensor.smarthus_status` – fx "Lader batteri · Lader elbil"
- `sensor.smarthus_savings_today` / `_month` / `_total` – besparelser i kr
- `binary_sensor.smarthus_ev_charge_now` m.fl. – hvad planen ønsker nu, med attributten `periods`
- `switch.smarthus_control_*` – slå styring til/fra pr. modul
- `number.smarthus_*` – antal timer, grader og temperaturer, kan ændres direkte fra dashboardet

## Dashboard
Se `dashboards/smarthus-dashboard.yaml`. Prisgrafen bruger [ApexCharts Card](https://github.com/RomRider/apexcharts-card) fra HACS.

---

## Sådan beregnes det

- **Købspris** = (spot + leverandørtillæg + nettarif + Energinet-tarif + elafgift) × 1,25 moms
- **Salgspris** = spot − fradrag ved salg
- **Nettarif** følger Tarifmodel 3.0: lav 00–06, høj 06–17 og 21–24, spids 17–21. Vinter er oktober–marts.
- **Planer** laves pr. døgn. Morgendagens priser kommer ca. kl. 13, og så opdateres planen automatisk. Priserne er i 15-minutters opløsning.
- **Elbil med SoC:** vælg bilens batteriniveau-sensor i opsætningen og indstil "Elbil: lad til" (mål-SoC), batteristørrelse og ladeeffekt. Ladetiden beregnes som (mål − nu) × batteri ÷ ladeeffekt ÷ 0,9 (ladetab), og laderen slukkes, når mål-SoC er nået – også ved "Lad elbil nu". Planen genberegnes hvert minut, så den følger bilens faktiske niveau.
- **Elbil uden SoC:** lader et fast antal timer ("Elbil: timer at lade") og vælger de billigste tidsrum i de 24 timer op til "klar kl.". Hvis morgendagens priser endnu ikke er kendt, venter den – men lader altid i de sidste timer før klar-tidspunktet som sikkerhed.
- **Besparelse (elbil, varmepumpe)** = kWh × (dagens gennemsnitspris − prisen da strømmen blev brugt).
- **Besparelse (batteri)** = afladet kWh × pris − opladet kWh × pris.
- **Værdi af solceller** = egetforbrug × købspris + eksport × salgspris.

Besparelser kræver, at du vælger energimålere (kWh, stigende) i opsætningen.

---

## Bemærk
- Når et modul er slået til, holder SmartHus enheden i den ønskede tilstand. Ændrer du manuelt termostaten, sætter SmartHus den tilbage inden for et par minutter. Slå modulet fra, hvis du vil styre manuelt.
- Beregnede besparelser er estimater.

Udviklet af SmartHus.dk · MIT-licens

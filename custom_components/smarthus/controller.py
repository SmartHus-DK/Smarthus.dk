"""SmartHus-styring: planlægger og styrer enhederne hvert minut."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FORECAST_SOURCE,
    FORECAST_AUTO,
    FORECAST_SOURCES,
    API_URL,
    S_HOTWATER_HOURS,
    CONF_HOTWATER_MIN_ENTITY,
    S_HOTWATER_MIN_TEMP,
    S_HOTWATER_WATCH_TEMP,
    EDS_DAYAHEAD_FROM,
    EDS_OLD_URL,
    OPEN_METEO_URL,
    CONF_SALE_FEE,
    CONF_SL_PRICE_VAT,
    CONF_SL_SPOT_VAT,
    CONF_VAT,
    DEFAULT_PRICES,
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_NORMAL,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_BATTERY_MODE_ENTITY,
    CONF_BATTERY_SCRIPT_CHARGE,
    CONF_BATTERY_SCRIPT_DISCHARGE,
    CONF_BATTERY_SCRIPT_NORMAL,
    CONF_BATTERY_SCRIPT_SELL_SOLAR,
    CONF_BATTERY_OPT_SELL_SOLAR,
    ACTION_SELL_SOLAR,
    ACTION_HOLD,
    CONF_BATTERY_SCRIPT_HOLD,
    CONF_BATTERY_OPT_HOLD,
    S_SOLAR_SELL_MARGIN,
    S_BATTERY_MAX_CHARGE_KW,
    S_BATTERY_GRID_BUFFER,
    S_BATTERY_CHARGE_HOURS,
    S_BATTERY_DISCHARGE_HOURS,
    S_BATTERY_CHARGE_FROM,
    S_BATTERY_MAX_SOC,
    BATTERY_SCRIPTS,
    CONF_HUAWEI_DEVICE,
    CONF_GROWATT_DEVICE,
    CONF_GROWATT_POWER,
    CONF_GROWATT_TYPE,
    DEFAULT_GROWATT_POWER,
    CONF_HUAWEI_POWER,
    CONF_BATTERY_POWER,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_POWER_SIGN,
    SIGN_CHARGE_POSITIVE,
    DEFAULT_HUAWEI_POWER,
    CONF_EXPORT_OPT_NORMAL,
    CONF_EXPORT_OPT_STOP,
    CONF_EXPORT_SCRIPT_START,
    CONF_EXPORT_SCRIPT_STOP,
    CONF_EXPORT_VALUE_NORMAL,
    CONF_EXPORT_VALUE_STOP,
    EXPORT_SCRIPTS,
    CONF_BATTERY_OPT_CHARGE,
    CONF_BATTERY_OPT_DISCHARGE,
    CONF_BATTERY_OPT_NORMAL,
    CONF_BATTERY_SOC,
    CONF_BATTERY_RESERVE,
    CONF_EXPORT_ENERGY,
    CONF_IMPORT_ENERGY,
    CONF_HEATING_CLIMATE,
    CONF_HEATING_TYPE,
    HEATING_TYPE_AIR,
    CONF_HEATING_INDOOR_TEMP,
    CONF_HEATING_SCRIPT_NORMAL,
    CONF_HEATING_SCRIPT_BOOST,
    CONF_HEATING_SCRIPT_REDUCE,
    S_HEATING_MIN_INDOOR,
    F_HEATING_OFF_REDUCE,
    CONF_HEATPUMP_ENERGY,
    S_BATTERY_MIN_SPREAD,
    CONF_OUTDOOR_TEMP,
    CONF_WEATHER,
    HEATING_OUTDOOR_HYSTERESIS,
    S_HEATING_MAX_OUTDOOR,
    CONF_HOTWATER_ENERGY,
    CONF_HOTWATER_ENTITY,
    CONF_HOTWATER_TEMP,
    CONF_GRID_POWER,
    CONF_GRID_IMPORT_POWER,
    CONF_GRID_EXPORT_POWER,
    CONF_GRID_POWER_SIGN,
    SIGN_IMPORT_POSITIVE,
    SURPLUS_CONFIRM_TICKS,
    TICK_SECONDS,
    CONF_SOLAR_POWER,
    F_HOTWATER_SURPLUS_SMART,
    F_HEATING_AUTO,
    F_BATTERY_SELL,
    F_BATTERY_FULL_CHARGE,
    F_BATTERY_OPTIMIZER,
    SURPLUS_STOP_IMPORT_KW,
    HOTWATER_HYSTERESIS,
    CONF_HOUSE_ENERGY,
    CONF_SOLAR_FORECAST,
    CONF_SOLAR_CURTAIL,
    CONF_SOLAR_ENERGY,
    DEFAULT_BATTERY_OPTIONS,
    DEFAULT_SETTINGS,
    DOMAIN,
    HEATING_BOOST,
    HEATING_REDUCE,
    MIN_COMMAND_INTERVAL,
    S_SOLAR_FORECAST_PCT,
    CONF_BATTERY_ACTIONS,
    MODULE_BATTERY,
    MODULE_HEATING,
    MODULE_HOTWATER,
    MODULE_MASTER,
    MODULE_SOLAR,
    MODULES,
    get_cars,
    S_BATTERY_CAPACITY,
    S_BATTERY_MIN_SOC,
    S_HEATING_BASE_TEMP,
    S_HEATING_BOOST_DEG,
    S_HEATING_REDUCE_DEG,
    S_HOTWATER_BOOST_TEMP,
    S_HOTWATER_NORMAL_TEMP,
    SIGNAL_TICK,
)
from .coordinator import SmartHusPriceCoordinator
from . import growatt as gw
from .planner import CHARGE_EFFICIENCY, full_charge_hours, CarInput, Plan, build_plan, car_ready, car_settings, heating_outdoor_ok
from .planner import BATTERY_MIN_BLOCK_H, BATTERY_MIN_GAP_H, BATTERY_ROUND_TRIP, cheapest_blocks, in_window
from .optimizer import OptSlot, morning_buffer, optimize
SOLAR_CHARGE_SURPLUS_KWH = 0.3   # forventet soloverskud i timen, hvor der ikke lades fra nettet
DAY_CYCLE_FROM_H, DAY_CYCLE_PEAK_H = 8, 15   # ladning midt på dagen (fra kl. 8) til aftenens top (fra kl. 15)
SELL_RESTART_KWH = 0.5   # kWh over reserven, før salg starter igen efter et stop
BATTERY_RECHARGE_MARGIN = 2.0   # %-point under højeste niveau, før batteriet lades fra nettet igen

from .profile import (default_profile, EFFICIENCY, TEMP_LEARN_DAYS, TempModel, fit_temperature, temperature_adjust,
                      HeatPumpModel, fit_heatpump, heatpump_hours, hour_averages, hdd, plan_hold, forecast_battery, LEARN_DAYS, SUN_END, SUN_START, Profile, Reserve, build_profile, calc_reserve,
                      expected_load, grid_charge_target, solar_surplus, battery_need_at, simulate_battery)
from .savings import SavingsCalculator
from .hotwater import cooling_rate, plan_demand
from .energy import EnergyBook, add_devices, layout_from_config, layout_from_energy_prefs, layout_signature, layout_stats
from .flow import energy_flow, to_kw
from .pricing import has_vat, vat_factor
from .plug import pick_connected, plug_state
from .solar import EvSolarState, regulate_ev
from .suncal import SolarCalibration, hour_key
from .weather import SunTracker, forecast_sensor_ids, merge_wh, pick_weather_entity, solar_by_hour, summarize_forecast

_LOGGER = logging.getLogger(__name__)

EV_CHANGE_GRACE_SEC = 90   # sekunder uden opstart efter man har ændret elbilens tidspunkt/mål
SAVINGS_SAVE_SEC = 300     # regnskabet skrives til disken mindst hvert 5. minut
HEAL_TODAY_DELAY_SEC = 120 # efter opstart: byg dagens tal op fra statistikken (når recorderen er klar)
RESTORE_DELAY_SEC = 10     # slås styringen til igen inden for 10 s, sendes der intet til enhederne
EV_FULL_IDLE_SEC = 900    # bilen stopper selv: tændt lader uden strømforbrug i 15 min = bilen er fuld
HUAWEI_DISCHARGE_KEY = "storage_maximum_discharging_power"
EV_SOC_WAIT_SEC = 300          # efter genstart: vent på bilens batteriniveau før opladning planlægges
EV_FAULT_RATIO = 0.8          # ladefejl: under 80 % af ladeeffekten …
EV_FAULT_SEC = 300            # … i 5 minutter
EV_FAULT_MAX_SOC = 90         # over 90 % sætter bilen selv strømmen ned
EASEE_FULL_A = 32             # Easee: fuld ladestrøm (laderen begrænser selv til sin maks.)
EV_PROTECT_KW = 1.0           # elbilen lader fra nettet: husbatteriet gemmes (ikke tømt ned i bilen)
HOLD_SUN_ON_KW = 0.15      # overskud fra solen, der får "gem batteriet" til at lade fra solen
HOLD_SUN_OFF_KW = 0.05
HOLD_SUN_ON_SEC = 120
HOLD_SUN_OFF_SEC = 180
EV_STRAY_KW = 0.5          # laderen trækker strøm, selv om den er slukket i Home Assistant
EV_STRAY_TICKS = 4         # … i 4 × 30 s → send sluk igen


def _num(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _water_temp(val) -> float | None:
    """Et realistisk tal for varmt vand (1–100 °C) – 0 eller tomme værdier ignoreres."""
    v = _num(val)
    return v if v is not None and 1.0 <= v <= 100.0 else None

_UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}


class SmartHusController:
    """Binder priser, planer, enheder og besparelser sammen."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 coordinator: SmartHusPriceCoordinator, config: dict) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.config = config
        self.tz = dt_util.get_default_time_zone()
        self.settings: dict[str, float] = dict(DEFAULT_SETTINGS)
        self.cars: list[dict] = get_cars(config)
        self.car_ids: list[str] = [car["id"] for car in self.cars]
        # Moduler: de faste + én pr. elbil (bilens id, fx "ev" eller "ev_tesla")
        self.modules: list[str] = [m for m in MODULES if m != "ev"] + self.car_ids
        self.enabled: dict[str, bool] = {MODULE_MASTER: True, **{m: False for m in MODULES},
                                         **{cid: False for cid in self.car_ids}}
        self.ev_force: dict[str, bool] = {cid: False for cid in self.car_ids}
        # Biler der deler en lader: {lader-entitet: [bil-id, ...]} (kun grupper med mindst 2 biler)
        groups: dict[str, list[str]] = {}
        for car in self.cars:
            groups.setdefault(car["charger"], []).append(car["id"])
        self.shared_chargers: dict[str, list[str]] = {ch: ids for ch, ids in groups.items() if len(ids) > 1}
        # Manuelt valg af tilsluttet bil pr. delt lader (gendannes af select-entiteten)
        self.connected_choice: dict[str, str] = {}
        self.connected: dict[str, bool] = {cid: True for cid in self.car_ids}
        self.connected_source: dict[str, str] = {}
        self.plan = Plan()
        self.savings = SavingsCalculator()
        self._savings_loaded = False
        self._savings_saved = 0.0          # hvornår regnskabet sidst blev skrevet til disken
        self.profile = default_profile()    # det, der planlægges efter: standardforbrug, indtil mønsteret er lært
        self.learned_profile = Profile()    # det, der er lært – det er kun det, kort og apps viser
        self.profile_updated: datetime | None = None
        self.reserve: Reserve | None = None
        self.reserve_holds = False  # True når salg fra batteriet er sat på pause for at gemme reserven
        self.battery_hold: dict | None = None  # beslutning om at gemme batteriet til de dyre timer
        self.battery_forecast: dict | None = None  # skøn: hvornår batteriet bruges, og hvad det sparer
        self.sell_solar: dict | None = None  # beslutning om at sælge solstrøm i stedet for at lade batteriet
        self.planned_charge: dict | None = None  # næste opladning fra nettet (kan aflæses på forhånd)
        self.grid_charge: dict | None = None  # hvor meget der lades fra nettet, når solen kan fylde resten
        self._unsub_learn = None
        self._unsub_learn_first = None
        self._unsub_temps = None
        self.temp_model = TempModel(reason="ikke lært endnu")
        self._temps: dict[datetime, float] = {}      # udetemperatur pr. lokal time (historik + vejrudsigt)
        self._temps_at: datetime | None = None
        self._house_hours: list = []
        self._hp_hours: list = []                     # varmepumpens egen måler pr. time (planlægges for sig)
        self.hp_model = HeatPumpModel()
        self._hp_avg: dict = {}
        self._unsub_sun_learn = None
        self._unsub_sun_learn_first = None
        self._unsub_energy = None
        self._unsub_energy_first = None
        self._energy_store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.energy")
        self.energy = EnergyBook()
        self.energy_error: str | None = None
        self._energy_busy = False
        self._price_cache: dict[datetime, tuple[float | None, float | None]] = {}
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.savings")
        self._last_cmd: dict[str, float] = {}
        # Sidste tilstand der er sendt via script (scripts køres kun ved skift)
        self._script_state: dict[str, str] = {}
        self.hold_sun = False                 # "gem batteriet", men solens overskud lader det (Growatt SPH)
        self._hold_sun_since: float | None = None
        self._born = time.monotonic()           # start – bilens batteriniveau ventes på (_soc_pending)
        # Varmt vand med temperaturføler: True når øvre grænse er nået
        self.hotwater_full = False
        self.heating_outdoor_ok = True     # varmestyringen er aktiv ift. udetemperaturen
        self.heating_too_cold = False      # frostsikring (luft-til-luft): for koldt inde til at spare
        self._heating_off_by_us = False    # SmartHus har slukket luft-til-luft-pumpen i sparetimer
        self._heating_paused_applied = False
        self._ev_charged: dict[str, tuple[str, float]] = {}
        self.ev_ready_mode: dict = {}  # bil → (tilstand, fast dato) for "klar kl."  # bil → (klar-tidspunkt, ladede timer)  # grundtemperaturen er sat for den nuværende pause
        self._hw_button_on = False
        # Fælles energimåler for varme og varmt vand: fordeles mellem de to
        self._heat_last: float | None = None
        self._heat_cum = {"heatpump": 0.0, "hotwater": 0.0}         # varmt vand via knap: trykket i den nuværende periode
        # Solprognose (Wh pr. periode) fra Forecast.Solar/Solcast – hentes hvert 15. minut
        self._forecast_wh: dict[str, float] = {}
        self.sun = SunTracker()        # faktisk sol i dag ÷ prognosen – justerer resten af dagens prognose
        self._forecast_key_is_end = False
        self._forecast_domain: str | None = None
        self._forecast_fetched = 0.0
        self.solar_cal = SolarCalibration()   # den indlærte forskel mellem solprognosen og anlægget
        self._solar_cal_store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.solar_calibration")
        self._fc_ids: dict[str, str] = {}
        # Opvarmning med solcelleoverskud
        self.hotwater_surplus = False
        self._surplus_count = 0
        self._hw_surplus_sec = 0.0
        self._hw_done_date: str | None = None   # dag hvor vandet er varmet op
        # Varmt vand efter behov: temperaturmålinger, afkøling og ekstra opvarmning
        self._hw_samples: list[tuple[datetime, float]] = []
        self._hw_seeded = False
        self.hw_rate: float | None = None
        self.hw_deadline: datetime | None = None
        self.hw_demand: list = []
        self.hw_demand_now = False
        # Indstillinger der slås til/fra på kortet (gendannes af kontakterne)
        self.flags: dict[str, bool] = {F_HOTWATER_SURPLUS_SMART: True}
        # Solcelleladning pr. elbil
        self.ev_solar: dict[str, EvSolarState] = {}
        self._ev_current_sent: dict[str, int] = {}
        # Når man ændrer elbilens tidspunkt/mål, ventes der lidt, før laderen startes – så man
        # kan nå at vælge "klar kl.", før en halvfærdig indstilling starter opladningen
        self._ev_hold: dict[str, float] = {}
        self._restore_pending: dict[str, object] = {}   # udskudt "normal drift", når styring slås fra
        self._ev_stray: dict[str, int] = {}              # tællere: lader slukket, men lader stadig
        # Bilens SoC fra fx Kia/Hyundai-skyen kan stå stille i lang tid, mens bilen lader.
        # {bil: {"base": seneste SoC fra bilen, "kwh": ladet siden da}} → skønnet SoC
        self._soc_est: dict[str, dict] = {}
        # Biler uden SoC-sensor: batteriniveau tastet ind af brugeren og skønnet videre ud fra
        # den ladede energi. {bil: {"base", "kwh", "over_h", "expires", "done"}}
        self.manual_soc: dict[str, dict] = {}
        self._ev_idle: dict[str, float] = {}       # siden hvornår laderen er tændt, men bilen ikke trækker strøm
        self._ev_full_key: dict[str, str] = {}     # uden SoC: bilen er fuld for dette klar-tidspunkt
        # Elbilens planlagte kvarterer, der er gået i gang – så de stadig kan ses på grafen bagefter
        self._ev_past: dict[str, set] = {}
        # Batteriets fremskrivning (bruges, gemmes, sol) regnes kun fra nu – de kvarterer, der er gået i gang,
        # huskes her, så grafen stadig viser dem bagefter. {navn: {start: Slot}}
        self._plan_past: dict[str, dict] = {}
        self._plan_done: dict[str, dict] = {}
        # "Lad nu" startet, mens SmartHus' styring af bilen er slået fra
        self._ev_manual: dict[str, bool] = {}
        self._unsub = None
        self._unsub_prices = None
        self.expected_unique_ids: set[str] = set()

    # --- Livscyklus --------------------------------------------------------
    async def async_load_savings(self) -> None:
        """Hent gemte besparelser – før sensorerne oprettes, så de ikke kortvarigt viser 0."""
        self.savings = SavingsCalculator(await self._store.async_load())
        try:
            self.energy = EnergyBook(await self._energy_store.async_load())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus: kunne ikke hente energioverblikket: %s", err)
        try:
            self.solar_cal = SolarCalibration(await self._solar_cal_store.async_load())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus: kunne ikke hente solprognosens læring: %s", err)
        self._savings_loaded = True

    async def async_start(self) -> None:
        if not self._savings_loaded:
            await self.async_load_savings()
        self._unsub = async_track_time_change(self.hass, self._async_tick, second=[5, 35])
        self._unsub_heal = async_call_later(self.hass, HEAL_TODAY_DELAY_SEC, self._async_heal_today)
        self._unsub_prices = self.coordinator.async_add_listener(self._on_prices)
        self._unsub_sl = None
        if self.coordinator.uses_stromligning:
            self._unsub_sl = async_track_state_change_event(
                self.hass, [*self.coordinator.sl_entities, self.coordinator.sl_spot], self._on_stromligning
            )
        if self.has(CONF_HOUSE_ENERGY):
            # Lær forbrugsmønsteret hver nat og kort efter opstart (når historikken er klar)
            self._unsub_learn = async_track_time_change(self.hass, self.async_learn, hour=0, minute=20, second=30)
            self._unsub_learn_first = async_call_later(self.hass, 60, self.async_learn)
            # Vejrudsigten (udetemperatur) hentes igen hver 3. time, så forbruget følger med, når det bliver koldere
            self._unsub_temps = async_track_time_change(self.hass, self._async_temps_tick, minute=7, second=40)
        if self.has(CONF_SOLAR_ENERGY) or self.has(CONF_SOLAR_POWER):
            # Sammenlign solprognosen med den faktiske produktion hver nat og kort efter opstart
            self._unsub_sun_learn = async_track_time_change(self.hass, self.async_learn_solar, hour=0, minute=25, second=30)
            self._unsub_sun_learn_first = async_call_later(self.hass, 75, self.async_learn_solar)
        # Energioverblik: bygges kort efter opstart og opdateres hver time (når timens statistik er klar)
        self._unsub_energy = async_track_time_change(self.hass, self.async_refresh_energy, minute=14, second=10)
        self._unsub_energy_first = async_call_later(self.hass, 90, self.async_refresh_energy)
        self.recalculate()

    async def async_import_solar_history(self, start_date=None) -> dict:
        """Genberegn solcelleregnskabet time for time ud fra Home Assistants langtidsstatistik.

        Bruger produktion, eksport og køb fra de valgte energimålere og den faktiske pris i
        hver time (Strømlignings historik, ellers spotpris fra Energi Data Service + prismodel).
        Kan køres igen når som helst – regnskabet bygges forfra, så intet tælles dobbelt.
        """
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        def ts_any(row) -> datetime:
            st = row.get("start")
            return datetime.fromtimestamp(st, dt_util.UTC) if isinstance(st, (int, float)) else st

        meters = self._solar_meters()
        if "solar" not in meters and "import" not in meters:
            raise ValueError("Der er ikke valgt en energimåler for solceller eller køb fra nettet")
        end = dt_util.now().replace(minute=0, second=0, microsecond=0)
        rec = get_instance(self.hass)
        if start_date:
            start = dt_util.start_of_local_day(start_date)
        else:
            # Så langt tilbage som målerne har historik (langtidsstatistik gemmes uden udløb)
            monthly = await rec.async_add_executor_job(
                statistics_during_period, self.hass, datetime(2000, 1, 1, tzinfo=dt_util.UTC), end,
                set(meters.values()), "month", {"energy": "kWh"}, {"change"},
            )
            firsts = [ts_any(rows[0]) for rows in monthly.values() if rows]
            if not firsts:
                raise ValueError("Ingen historik fundet for de valgte energimålere")
            start = min(firsts)
        rows, last, hours_found = await self._async_solar_rows(meters, start, end, first_common=not start_date)
        if not hours_found:
            raise ValueError("Ingen historik fundet for de valgte energimålere")
        summary = self.savings.rebuild_solar(rows, dt_util.now(), last)
        await self._store.async_save(self.savings.data)
        async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))
        _LOGGER.info("SmartHus: solcellehistorik genberegnet: %s", summary)
        return summary

    def _solar_meters(self) -> dict[str, str]:
        return {n: self.config.get(k) for n, k in (("solar", CONF_SOLAR_ENERGY), ("export", CONF_EXPORT_ENERGY),
                                                    ("import", CONF_IMPORT_ENERGY)) if self.config.get(k)}

    async def _async_solar_rows(self, meters: dict[str, str], start: datetime, end: datetime,
                                first_common: bool = False) -> tuple[list[dict], dict[str, float], bool]:
        """Timer med produktion/eksport/køb fra langtidsstatistikken + timens pris, og målernes værdi
        ved slutningen af perioden."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        rec = get_instance(self.hass)
        stats = await rec.async_add_executor_job(
            statistics_during_period, self.hass, start, end, set(meters.values()), "hour",
            {"energy": "kWh"}, {"change", "state"},
        )
        def ts(row) -> datetime:
            st = row.get("start")
            return datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st)

        no_stats = [e for e in meters.values() if not stats.get(e)]
        if no_stats and first_common:
            raise ValueError(
                "Ingen langtidsstatistik for: " + ", ".join(no_stats)
                + ". Målerne skal have state_class 'total' eller 'total_increasing' (de kan ses i Energi-panelet).")
        hours: dict[datetime, dict] = {}
        last: dict[str, float] = {}
        for name, entity_id in meters.items():
            rows = stats.get(entity_id, [])
            for row in rows:
                if row.get("change") is not None:
                    hours.setdefault(ts(row), {})[name] = float(row["change"])
            if rows and rows[-1].get("state") is not None:
                last[name] = float(rows[-1]["state"])  # målerens værdi ved historikkens slutning
        if not hours:
            return [], last, False
        if first_common:
            # Start først, når alle målere har historik – ellers ser fx timer uden eksportmåler
            # ud som 100 % egetforbrug
            firsts = [ts(rows[0]) for rows in (stats.get(e, []) for e in meters.values()) if rows]
            begin = max(firsts)
            hours = {t: v for t, v in hours.items() if t >= begin}

        prices = await self._async_hour_prices(sorted(hours))
        rows = []
        for t, v in hours.items():
            price, sale = prices.get(t, (None, None))
            rows.append({"start": t, "produced": v.get("solar"), "export": v.get("export"),
                         "import": v.get("import"), "price": price, "sale": sale})
        return rows, last, True

    async def _async_heal_today(self, _now=None) -> None:
        """Efter opstart: byg dagens solcelle- og købstal op fra langtidsstatistikken.

        Lukkede Home Assistant ikke pænt ned, er de gemte tal ældre end i dag, og alt siden
        sidste gemning ville ellers mangle (fx "Købt fra nettet i dag" falder til 0).
        """
        meters = self._solar_meters()
        if not meters:
            return
        try:
            now = dt_util.now()
            start = dt_util.start_of_local_day(now)
            end = now.replace(minute=0, second=0, microsecond=0)
            if end <= start:
                return
            rows, last, found = await self._async_solar_rows(meters, start, end)
            if not found:
                return
            res = self.savings.replace_today(rows, now, last)
            await self._store.async_save(self.savings.data)
            self._savings_saved = time.monotonic()
            async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))
            _LOGGER.info("SmartHus: dagens tal genopbygget fra statistikken: %s", res)
        except Exception as err:  # noqa: BLE001 – må ikke stoppe opstarten
            _LOGGER.warning("SmartHus: kunne ikke genopbygge dagens tal: %s", err)

    async def _async_hour_prices(self, hours: list[datetime]) -> dict[datetime, tuple[float | None, float | None]]:
        """Købs- og salgspris (kr/kWh) for hver time: Strømlignings historik, ellers spotpris + prismodel.

        Afsluttede timer gemmes i hukommelsen, så de ikke hentes igen ved næste opdatering.
        """
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        out = {t: self._price_cache[t] for t in hours if t in self._price_cache}
        todo = sorted(t for t in hours if t not in out)
        if not todo:
            return out
        start, end = todo[0], todo[-1] + timedelta(hours=1)

        def ts(row) -> datetime:
            st = row.get("start")
            return datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st)

        sl_price = sl_spot = None
        price_ids: set[str] = set()
        if self.coordinator.uses_stromligning:
            sl_price, sl_spot = self.coordinator.sl_entities[0], self.coordinator.sl_spot
            price_ids = {e for e in (sl_price, sl_spot) if e}
        pstats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, price_ids, "hour", None, {"mean", "state"},
        ) if price_ids else {}

        def means(entity_id) -> dict[datetime, float]:
            # Pris-sensorer med state_class "total" har ingen middelværdi – brug så timens pris
            res = {}
            for r in pstats.get(entity_id, []) if entity_id else []:
                v = r.get("mean") if r.get("mean") is not None else r.get("state")
                if v is not None:
                    res[ts(r)] = float(v)
            return res

        buy, spot = means(sl_price), means(sl_spot)
        # Samme momsvalg som de løbende priser (inkl./ekskl. moms i opsætningen)
        want_vat = bool(self.config.get(CONF_VAT, DEFAULT_PRICES[CONF_VAT]))
        f = vat_factor(has_vat(self.config.get(CONF_SL_PRICE_VAT), sl_price), want_vat)
        buy = {t: p * f for t, p in buy.items()}
        spot_vat = has_vat(self.config.get(CONF_SL_SPOT_VAT), sl_spot)
        fee = float(self.config.get(CONF_SALE_FEE, DEFAULT_PRICES[CONF_SALE_FEE]))
        missing = [t for t in todo if t not in buy or t not in spot]
        eds = await self._async_eds_hourly_spot(missing[0], missing[-1]) if missing else {}
        settled = dt_util.now() - timedelta(hours=2)
        for t in todo:
            s_eds = eds.get(t)
            price = buy.get(t)
            if price is None and s_eds is not None:
                price = self.coordinator.model.buy_price(s_eds, t, historic=True)
            if t in spot:
                sale = (spot[t] / 1.25 if spot_vat else spot[t]) - fee
            elif s_eds is not None:
                sale = self.coordinator.model.sale_price(s_eds)
            else:
                sale = None
            out[t] = (price, sale)
            if price is not None and sale is not None and t < settled:
                self._price_cache[t] = (price, sale)
        return out

    async def _async_energy_layout(self) -> dict | None:
        """Målerne til energioverblikket: Home Assistants Energi-dashboard, ellers SmartHus' egne målere."""
        prefs = None
        try:
            from homeassistant.components.energy.data import async_get_manager
            manager = await async_get_manager(self.hass)
            prefs = manager.data
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("SmartHus: Energi-dashboardet kunne ikke læses: %s", err)
        names = {}
        for d in (prefs or {}).get("device_consumption") or []:
            st = self.hass.states.get(d.get("stat_consumption") or "")
            if st:
                names[st.entity_id] = st.name
        g = self.config.get
        cars = [(f"Lader · {c.get('name') or 'Elbil'}", c.get("energy"), "ev") for c in self.cars]
        layout = layout_from_energy_prefs(prefs, names)
        if layout:
            # Elbilens lader fra SmartHus kommer med, hvis den ikke står i Energi-dashboardet
            known = {d.get("stat_consumption") for d in (prefs or {}).get("device_consumption") or []}
            return add_devices(layout, cars, known)
        devices = [("Varmepumpe", g(CONF_HEATPUMP_ENERGY), "heatpump"), ("Varmt vand", g(CONF_HOTWATER_ENERGY), "hotwater")]
        devices += cars
        return layout_from_config({"import": g(CONF_IMPORT_ENERGY), "export": g(CONF_EXPORT_ENERGY),
                                   "solar": g(CONF_SOLAR_ENERGY), "battery_in": g(CONF_BATTERY_CHARGE_ENERGY),
                                   "battery_out": g(CONF_BATTERY_DISCHARGE_ENERGY)}, devices)

    async def async_refresh_energy(self, *_args, full: bool = False) -> None:
        """Opdatér energioverblikket ud fra langtidsstatistikken.

        Første gang (eller når målerne ændres) bygges hele historikken. Derefter genberegnes kun
        perioden fra starten af den måned, hvor ugen begynder – så uge og måned altid er hele.
        """
        if self._energy_busy:
            return
        self._energy_busy = True
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period

            layout = await self._async_energy_layout()
            if not layout:
                self.energy_error = "Ingen energimålere fundet – sæt Energi-dashboardet op i Home Assistant"
                return
            ids = layout_stats(layout)
            rec = get_instance(self.hass)
            now = dt_util.now()
            end = now.replace(minute=0, second=0, microsecond=0)
            rebuild = full or self.energy.data.get("sig") != layout_signature(layout) or not self.energy.data.get("months")
            if rebuild:
                monthly = await rec.async_add_executor_job(
                    statistics_during_period, self.hass, datetime(2000, 1, 1, tzinfo=dt_util.UTC), end,
                    ids, "month", {"energy": "kWh"}, {"change"},
                )
                firsts = []
                for rows in monthly.values():
                    if rows:
                        st = rows[0].get("start")
                        firsts.append(datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st))
                if not firsts:
                    self.energy_error = "Ingen historik for energimålerne endnu"
                    return
                first = min(firsts)
                start = first.astimezone(self.tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                monday = (now - timedelta(days=now.weekday())).date()
                start = dt_util.start_of_local_day(min(monday, now.date().replace(day=1)).replace(day=1))
            # Hent i bidder på ca. et kvartal, så store historikker ikke belaster databasen på én gang
            rows_by_hour: dict[datetime, dict[str, float]] = {}
            chunk = start
            while chunk < end:
                stop = min(chunk + timedelta(days=92), end)
                stats = await rec.async_add_executor_job(
                    statistics_during_period, self.hass, chunk, stop, ids, "hour", {"energy": "kWh"}, {"change"},
                )
                for sid, rows in stats.items():
                    for r in rows:
                        if r.get("change") is None:
                            continue
                        st = r.get("start")
                        t = datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st)
                        rows_by_hour.setdefault(t, {})[sid] = float(r["change"])
                chunk = stop
            if rebuild:
                # Regnskabet starter, når nettets (ellers solcellernes) måler har historik. Enheder med
                # længere historik ville ellers få mere forbrug end hele huset.
                base = set(layout.get("import") or layout.get("solar") or [])
                begin = min((t for t, kwh in rows_by_hour.items() if base & kwh.keys()), default=None)
                if begin is not None:
                    rows_by_hour = {t: v for t, v in rows_by_hour.items() if t >= begin}
            prices = await self._async_hour_prices(sorted(rows_by_hour))
            rows = [{"start": t, "kwh": kwh, "price": prices.get(t, (None, None))[0], "sale": prices.get(t, (None, None))[1]}
                    for t, kwh in rows_by_hour.items()]
            self.energy.apply(layout, rows, start, rebuild, now)
            self.energy_error = None
            await self._energy_store.async_save(self.energy.data)
            async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))
            if rebuild:
                _LOGGER.info("SmartHus: energioverblik bygget fra %s (%s timer)", start.date(), len(rows))
        except Exception as err:  # noqa: BLE001
            self.energy_error = f"Energioverblikket kunne ikke opdateres: {err}"
            _LOGGER.warning("SmartHus: %s", self.energy_error)
        finally:
            self._energy_busy = False

    async def _async_eds_hourly_spot(self, first: datetime, last: datetime) -> dict[datetime, float]:
        """Spotpris pr. time (kr/kWh, uden moms) fra Energi Data Service for en periode."""
        import asyncio
        import json

        out: dict[datetime, list[float]] = {}
        day = first.date()
        while day <= last.date():
            # Energi Data Service: "Elspotprices" (timepriser) til og med 30/9 2025,
            # derefter "DayAheadPrices" (kvarterpriser)
            new_from = EDS_DAYAHEAD_FROM
            to = min(day + timedelta(days=31), last.date() + timedelta(days=1))
            old_ds = day < new_from
            if old_ds:
                to = min(to, new_from)
            url, tcol, pcol = (EDS_OLD_URL, "HourUTC", "SpotPriceDKK") if old_ds else (API_URL, "TimeUTC", "DayAheadPriceDKK")
            params = {"start": day.isoformat(), "end": to.isoformat(),
                      "filter": json.dumps({"PriceArea": [self.coordinator.area]}),
                      "columns": f"{tcol},{pcol}", "sort": f"{tcol} ASC", "limit": "10000"}
            try:
                async with asyncio.timeout(60):
                    async with self.coordinator._session.get(url, params=params) as resp:  # noqa: SLF001
                        resp.raise_for_status()
                        payload = await resp.json()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SmartHus: kunne ikke hente historiske spotpriser %s–%s: %s", day, to, err)
                payload = {}
            for r in payload.get("records", []):
                if r.get(pcol) is None or not r.get(tcol):
                    continue
                t = datetime.fromisoformat(r[tcol])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt_util.UTC)
                t = dt_util.as_local(t)
                out.setdefault(t.replace(minute=0, second=0, microsecond=0), []).append(r[pcol] / 1000)
            day = to
        return {t: sum(v) / len(v) for t, v in out.items()}

    async def async_learn(self, *_args) -> None:
        """Lær husets forbrug pr. time ud fra de seneste dages historik (langtidsstatistik)."""
        house = self.config.get(CONF_HOUSE_ENERGY)
        if not house:
            return
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
        except ImportError:
            _LOGGER.warning("SmartHus: historik (recorder) er ikke tilgængelig – kan ikke lære forbrugsmønsteret")
            return
        end = dt_util.now().replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=TEMP_LEARN_DAYS)        # længere historik til varmebehovet
        profile_from = end - timedelta(days=LEARN_DAYS)      # mønsteret selv: de seneste 14 dage
        ev_ids = {c["energy"] for c in self.cars if c.get("energy")}
        hp_id = self.config.get(CONF_HEATPUMP_ENERGY)
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, start, end, {house} | ev_ids | ({hp_id} if hp_id else set()), "hour",
                {"energy": "kWh"}, {"change"},
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus kunne ikke hente historik for %s: %s", house, err)
            return

        def series(entity_id: str) -> dict[datetime, float]:
            out: dict[datetime, float] = {}
            for row in stats.get(entity_id, []):
                st, change = row.get("start"), row.get("change")
                if st is None or change is None:
                    continue
                ts = datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st)
                out[ts] = float(change)
            return out

        house_hours = series(house)
        for ev in ev_ids:  # elbilen planlægges for sig og skal ikke med i husets forbrug
            for ts, kwh in series(ev).items():
                if ts in house_hours:
                    house_hours[ts] = max(house_hours[ts] - kwh, 0.0)
        # Varmepumpe med egen måler: planlægges for sig (temperatur og SmartHus' spare-/ekstra-timer) –
        # huset lærer resten. Kun timer, hvor varmepumpens måler har data.
        hp = series(hp_id) if hp_id else {}
        self._hp_hours = sorted((t, k) for t, k in hp.items() if t in house_hours)
        for ts, kwh in hp.items():
            if ts in house_hours:
                house_hours[ts] = max(house_hours[ts] - kwh, 0.0)
        self.learned_profile = build_profile(sorted((t, k) for t, k in house_hours.items() if t >= profile_from))
        self._hp_avg = hour_averages([(t, k) for t, k in self._hp_hours if t >= profile_from]) if self._hp_hours else {}
        if self._hp_avg:   # mønsteret viser hele huset: læg varmepumpens gennemsnit oven i
            for we, attr in ((False, "weekday"), (True, "weekend")):
                setattr(self.learned_profile, attr, [v + a for v, a in zip(getattr(self.learned_profile, attr), self._hp_avg[we])])
        self.profile = self.learned_profile if self.learned_profile.ready else default_profile()
        self._house_hours = sorted(house_hours.items())
        self._profile_from = profile_from
        await self._async_update_temps(force=True)
        self.profile_updated = dt_util.now()
        if not self.learned_profile.ready:
            _LOGGER.info("SmartHus: kun %s dage med historik for %s – bruger standardforbrug, til der er 2 dage",
                         self.learned_profile.days, house)
        self.recalculate()

    async def _async_fetch_temps(self) -> dict[datetime, float]:
        """Udetemperatur pr. time fra Open-Meteo: de seneste 28 dage og 3 dage frem (kun husets omtrentlige position)."""
        import asyncio

        lat, lon = getattr(self.hass.config, "latitude", None), getattr(self.hass.config, "longitude", None)
        if lat is None or lon is None:
            return {}
        params = {"latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "hourly": "temperature_2m",
                  "past_days": str(TEMP_LEARN_DAYS + 1), "forecast_days": "3", "timeformat": "unixtime", "timezone": "GMT"}
        async with asyncio.timeout(30):
            async with self.coordinator._session.get(OPEN_METEO_URL, params=params) as resp:  # noqa: SLF001
                resp.raise_for_status()
                data = await resp.json()
        h = data.get("hourly") or {}
        return {datetime.fromtimestamp(t, self.tz): float(v)
                for t, v in zip(h.get("time") or [], h.get("temperature_2m") or []) if v is not None}

    async def _async_update_temps(self, force: bool = False) -> None:
        """Hent udetemperaturen, lær husets varmebehov og justér forbrugsmønsteret efter vejrudsigten."""
        now = dt_util.now()
        if not force and self._temps_at and now - self._temps_at < timedelta(hours=3):
            return
        try:
            temps = await self._async_fetch_temps()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus: kunne ikke hente udetemperatur (Open-Meteo): %s", err)
            temps = {}
        if temps:
            self._temps, self._temps_at = temps, now
        if not self._temps or not self._house_hours:
            return
        self.temp_model = fit_temperature(self._house_hours, self._temps, getattr(self, "_profile_from", None))
        if self._hp_hours:
            self.hp_model = fit_heatpump(self._hp_hours, self._temps, now.astimezone(self.tz).date(),
                                         getattr(self, "_profile_from", None))
        self.recalculate()          # justeringen regnes i recalculate, når planen (spare-/ekstra-timer) er klar

    def _apply_temp_adjust(self) -> None:
        """Justér forbrugsmønsteret efter vejrudsigten – og varmepumpens forbrug efter SmartHus' plan."""
        p = self.learned_profile
        if not self._temps:
            p.adjust = {}
            return
        now = dt_util.now().astimezone(self.tz)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        future = {t: v for t, v in self._temps.items() if t >= today}
        adj = temperature_adjust(self.temp_model, future)
        hp = self.hp_model
        if hp.active and self._hp_avg:
            plan_on = self.active(MODULE_HEATING)
            for i in range(3):
                day = today + timedelta(days=i)
                temps = [v for t, v in future.items() if t.date() == day.date()]
                if len(temps) < 20:
                    continue
                mean = sum(hdd(v) for v in temps) / len(temps)
                hrs = lambda slots: {s.start.astimezone(self.tz).hour for s in slots  # noqa: E731
                                     if s.start.astimezone(self.tz).date() == day.date()}
                red = hrs(self.plan.heating_reduce) if plan_on else set()
                boo = hrs(self.plan.heating_boost) if plan_on else set()
                vals = heatpump_hours(hp, day, mean, red, boo)
                avg = self._hp_avg[day.weekday() >= 5]
                for h in range(24):
                    adj[(day.date(), h)] = adj.get((day.date(), h), 0.0) + vals[h] - avg[h]
        p.adjust = adj
        if self.profile is not p:
            self.profile.adjust = {}

    async def _async_temps_tick(self, *_args) -> None:
        await self._async_update_temps()

    def heatpump_day(self, day) -> float | None:
        """Varmepumpens forventede forbrug (kWh) en given dag – None = ingen model/udsigt."""
        if not self.hp_model.active:
            return None
        temps = [v for t, v in self._temps.items() if t.date() == day]
        if len(temps) < 20:
            return None
        return round(self.hp_model.day_kwh(sum(hdd(v) for v in temps) / len(temps)), 1)

    def temp_extra(self, day) -> float | None:
        """Ekstra forbrug (kWh) en given dag pga. temperaturen i forhold til normalt – None = ingen model/udsigt."""
        if not (self.temp_model.active or self.hp_model.active) or not self.learned_profile.adjust:
            return None
        vals = [v for (d, _h), v in self.learned_profile.adjust.items() if d == day]
        return round(sum(vals), 1) if vals else None

    def _reserve_until(self, now: datetime) -> datetime:
        """Hvor længe skal batteriet række? Til næste planlagte opladning – ellers til den sidste kendte pris.
        (Ikke "næste billige tidsrum": det kunne være midt på dagen, så aftenens forbrug ikke blev regnet med.)"""
        charge = [s.start for s in self.plan.battery_charge if s.start > now]
        if charge:
            return min(charge)
        future = [s for s in (self.coordinator.data or []) if s.start > now]
        if future:
            return max(s.end for s in future)
        local = now.astimezone(self.tz)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(now.tzinfo)

    async def async_learn_solar(self, *_args) -> None:
        """Lær forskellen mellem solprognosen og den faktiske produktion ud fra statistikken."""
        if not self._forecast_domain:
            return
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
            from homeassistant.helpers import entity_registry as er
        except ImportError:
            return
        meter = self.config.get(CONF_SOLAR_ENERGY)
        power = None if meter else self.config.get(CONF_SOLAR_POWER)
        if not meter and not power:
            return
        # Prognosens egen effekt-sensor har historikken fra før læringen begyndte
        reg = er.async_get(self.hass)
        past_ids = {e.entity_id for entry in self.hass.config_entries.async_entries(self._forecast_domain)
                    for e in er.async_entries_for_config_entry(reg, entry.entry_id)
                    if e.entity_id.endswith("power_production_now")}
        now = dt_util.now().astimezone(self.tz)
        ids = {meter or power} | past_ids
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, now - timedelta(days=30), now, ids, "hour",
                {"energy": "kWh", "power": "kW"}, {"change", "mean"},
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus kunne ikke hente solproduktionens historik: %s", err)
            return

        def series(entity_id: str, field: str) -> dict[str, float]:
            out: dict[str, float] = {}
            for row in stats.get(entity_id, []):
                st, val = row.get("start"), row.get(field)
                if st is None or val is None:
                    continue
                ts = datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st)
                out[hour_key(ts)] = float(val)
            return out

        past: dict[str, float] = {}
        for entity_id in past_ids:
            for key, kw in series(entity_id, "mean").items():
                past[key] = past.get(key, 0.0) + kw
        self.solar_cal.backfill(past)
        self.solar_cal.learn(series(meter, "change") if meter else series(power, "mean"), now)
        await self._solar_cal_store.async_save(self.solar_cal.data)
        self.recalculate()

    def _update_reserve(self) -> None:
        self.reserve, self.reserve_holds = None, False
        self.battery_hold = None
        self.battery_forecast = None
        self.planned_charge = None
        self.plan.battery_use = []
        self.plan.battery_solar = []
        self.plan.solar_cover = []
        self._hold_prev = {s.start for s in self.plan.battery_hold} or getattr(self, "_hold_prev", set())
        self.plan.battery_hold = []
        self.sell_solar = None
        self.grid_charge = None
        if not self.has(CONF_HOUSE_ENERGY) or not self.has_battery_control or not self.profile.ready:
            return
        now = dt_util.utcnow()
        until = min(self._reserve_until(now), now + timedelta(hours=24))
        self.reserve = calc_reserve(
            self.profile, now.astimezone(self.tz), until.astimezone(self.tz),
            soc=self._float_state(self.config.get(CONF_BATTERY_SOC)),
            capacity=self.settings[S_BATTERY_CAPACITY], min_soc=self.settings[S_BATTERY_MIN_SOC],
            solar_remaining=((self._energy_kwh(CONF_SOLAR_FORECAST) if self.has(CONF_SOLAR_FORECAST) and not self.solar_cal.active
                              else self.solar_forecast.get("remaining_kwh")) or 0) * self.solar_factor_today,
        )
        # Sælg kun det, der er ud over reserven. Er salget stoppet ved reserven, startes det først igen,
        # når der er lidt at sælge – ellers skifter batteriet frem og tilbage hvert minut.
        sellable = self.reserve.sellable_kwh
        if sellable is not None and sellable <= 0.05:
            self._sell_stopped = True
        elif sellable is None or sellable >= SELL_RESTART_KWH:
            self._sell_stopped = False
        if self.plan.battery_action == ACTION_DISCHARGE and self.__dict__.get("_sell_stopped"):
            self.plan.battery_action = ACTION_NORMAL
            self.reserve_holds = True
        if not self._optimize_battery(now):
            self._decide_grid_charge(now)
            self._decide_sell_solar(now)
            self._decide_hold(now, until)
        else:
            self._decide_sell_solar(now)
        self._forecast_battery(now)

    @property
    def full_charge_hours(self) -> float | None:
        """"Lad batteriet helt op": timer der skal til fra laveste til højeste niveau ved den indtastede
        maks. ladeeffekt (inkl. ca. 5 % tab), rundet op til hele kvarterer. None = slået fra / ingen effekt."""
        kw = float(self.settings.get(S_BATTERY_MAX_CHARGE_KW, 0) or 0)
        if not self.flags.get(F_BATTERY_FULL_CHARGE) or kw <= 0:
            return None
        return full_charge_hours(self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MIN_SOC],
                                 self.settings[S_BATTERY_MAX_SOC], kw)

    def _night_need(self, now: datetime, until: datetime | None = None) -> dict | None:
        """Hvor meget batteriet skal have, når solen står op, for at klare huset resten af dagen og aftenen –
        regnet time for time med husets forbrugsmønster og solprognosen. None = mangler data."""
        solar = self._solar_hours()
        if solar is None or not self.profile.ready:
            return None
        cap, mx, mn = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MAX_SOC], self.settings[S_BATTERY_MIN_SOC]
        if cap <= 0:
            return None
        floor, full = cap * mn / 100, cap * mx / 100
        local = now.astimezone(self.tz)
        day = local if local.hour < SUN_START else local + timedelta(days=1)
        sun = day.replace(hour=SUN_START, minute=0, second=0, microsecond=0)
        end = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if until is None:
            # Kun frem til næste opladning fra nettet samme dag (fx kl. 21:30) – den dækker resten af aftenen
            nxt = [s.start.astimezone(self.tz) for s in self.plan.battery_charge
                   if sun < s.start.astimezone(self.tz) < end]
            until = min(nxt) if nxt else end
        end = min(max(until.astimezone(self.tz), sun), end)
        need = battery_need_at(self.profile, solar, sun, end, floor=floor, full=full)
        return {"sun": sun, "need_at_sun": need, "floor": floor, "full": full, "solar": solar}

    def _sell_solar_better(self, day: datetime, price: float) -> tuple[float | None, bool]:
        """(salgspris midt på dagen, om det slår at lade til `price` og i stedet sælge solstrømmen)."""
        mid = [s for s in (self.coordinator.data or [])
               if s.start.astimezone(self.tz).date() == day.date() and 10 <= s.start.astimezone(self.tz).hour < 15]
        sale_mid = sum(s.sale for s in mid) / len(mid) if mid else None
        margin = float(self.settings.get(S_SOLAR_SELL_MARGIN, 0.10))
        return sale_mid, bool(self.has_sell_solar and sale_mid is not None and sale_mid - price / EFFICIENCY >= margin)

    def _charge_economics(self, now: datetime) -> dict | None:
        """Kan det betale sig at lade fra nettet? De billigste kommende ladetimer (inden for ladetidsrummet)
        mod de dyreste kommende timer bagefter – med tabet regnet med og "min. prisforskel" som krav.

        Koder: "power" (maks. ladeeffekt ikke sat), "price" (gevinsten er under min. prisforskel),
        "ok" (det kan betale sig – så skyldes en manglende opladning, at batteriet og solen har nok)."""
        if float(self.settings.get(S_BATTERY_MAX_CHARGE_KW, 0) or 0) <= 0:
            return {"code": "power"}
        tz = self.tz
        data = [sl for sl in (self.coordinator.data or []) if sl.end > now]
        if len(data) < 8:
            return None
        dear = cheapest_blocks(data, max(float(self.settings.get(S_BATTERY_DISCHARGE_HOURS, 3) or 3), 1.0),
                               BATTERY_MIN_BLOCK_H, most_expensive=True)
        if not dear:
            return None
        dear_start = min(sl.start for sl in dear)
        before = in_window([sl for sl in data if sl.end <= dear_start], tz,
                           float(self.settings.get(S_BATTERY_CHARGE_FROM, 0)), float(self.settings.get("battery_charge_to", 24)))
        if len(before) < 4:
            return None
        hours = max(float(self.settings.get(S_BATTERY_CHARGE_HOURS, 1) or 1), 1.0)
        cheap = cheapest_blocks(before, min(hours, len(before) / 4), BATTERY_MIN_BLOCK_H) or before
        c = sum(sl.price for sl in cheap) / len(cheap)
        d = sum(sl.price for sl in dear) / len(dear)
        spread = float(self.settings.get(S_BATTERY_MIN_SPREAD, 0.0))
        gain = d - c / BATTERY_ROUND_TRIP
        return {"code": "price" if gain < spread else "ok", "cheap_price": round(c, 2), "dear_price": round(d, 2),
                "gain": round(gain, 2), "min_spread": round(spread, 2), "loss_pct": round((1 - BATTERY_ROUND_TRIP) * 100),
                "cheap_from": min(sl.start for sl in cheap).isoformat(), "dear_from": dear_start.isoformat()}

    def _battery_why(self, now: datetime) -> dict | None:
        """Forklaring til infoboksen under planen: hvorfor batteriet gemmes / ikke lades fra nettet,
        og om forbruget eller solen i dag afviger fra det forventede."""
        if not self.active(MODULE_BATTERY) or self.plan.current is None:
            return None
        p = self.plan
        local = now.astimezone(self.tz)
        out: dict = {"state": {ACTION_HOLD: "hold", ACTION_CHARGE: "charge", ACTION_DISCHARGE: "sell"}.get(p.battery_action, "normal"),
                     "planned_charge": bool(self.planned_charge), "now_price": round(p.current.price, 2)}
        pc = self.planned_charge or {}
        if pc:
            out["charge_plan"] = {"start": pc.get("start"), "end": pc.get("end"), "kwh": pc.get("kwh")}
        # Gemmes: til hvilke (dyre) timer?
        use = sorted((sl for sl in (p.battery_use or []) if sl.start > now), key=lambda sl: sl.start)
        if out["state"] == "hold" and use:
            first = use[0].start
            dear = [sl for sl in use if sl.start < first + timedelta(hours=4)]
            hold = self.__dict__.get("battery_hold") or {}
            out["hold_for"] = {"from": first.isoformat(), "price": round(sum(sl.price for sl in dear) / len(dear), 2),
                               "usable_kwh": hold.get("usable_kwh")}
        # Forbruget i dag (hele timer) mod det forventede – uden elbilen
        hrs = (getattr(self, "energy", None) and self.energy.data.get("hours")) or {}
        if hrs.get("date") == local.date().isoformat() and self.profile.ready:
            ev = [d["id"] for d in (self.energy.data.get("layout") or {}).get("devices") or [] if d.get("kind") == "ev"]
            done = [(int(k), v) for k, v in hrs.items() if k != "date" and isinstance(v, dict) and "house" in v]
            if done:
                actual = sum(max(v.get("house", 0.0) - sum(v.get(i, 0.0) for i in ev), 0.0) for _, v in done)
                expected = sum(self.profile.hours_for(local)[h] for h, _ in done)
                out["usage"] = {"actual_kwh": round(actual, 1), "expected_kwh": round(expected, 1),
                                "delta_kwh": round(actual - expected, 1)}
        # Solen i dag mod prognosen
        sun = getattr(self, "sun", None)
        if sun is not None and sun.ratio is not None and (sun.forecast or 0) >= 1:
            out["solar"] = {"ratio": round(sun.ratio, 2), "measured_kwh": round(sun.measured, 1),
                            "forecast_kwh": round(sun.forecast, 1)}
        out["charge"] = self._charge_economics(now)
        return out

    def _drop_solar_charge(self, now: datetime) -> None:
        """Lad ikke fra nettet i timer, hvor solen forventes at give overskud – så fylder solen batteriet,
        og nettets strøm ville bare skubbe solstrømmen ud til salg til en lavere pris.
        (Planen vælger døgnets billigste timer uden at kende solen; fx kl. 11–13 på en solrig dag.)"""
        p = self.plan
        if not p.battery_charge or not self.profile.ready:
            return
        solar = self._solar_hours()
        if not solar:
            return
        drop = set()
        for sl in p.battery_charge:
            if sl.end <= now:
                continue
            h = sl.start.astimezone(self.tz).replace(minute=0, second=0, microsecond=0)
            surplus = (solar.get(h) or 0.0) - self.profile.hours_for(h)[h.hour]
            if surplus >= SOLAR_CHARGE_SURPLUS_KWH and sl.price > sl.sale:
                drop.add(sl)
        if not drop:
            return
        p.battery_charge = [sl for sl in p.battery_charge if sl not in drop]
        if p.current in drop and p.battery_action == ACTION_CHARGE:
            p.battery_action = ACTION_NORMAL         # solen lader – batteriet tager overskuddet selv

    def _plan_day_cycle(self, now: datetime) -> None:
        """Ekstra opladning midt på dagen: er der en billig dal før aftenens dyre top, og er forskellen
        (inkl. ca. 10 % tab) mindst "min. prisforskel", lades lige nok til at dække toppen – minus det,
        solen selv lægger i batteriet. Nattens opladning er uændret."""
        self.day_cycle = None
        p = self.plan
        kw = float(self.settings.get(S_BATTERY_MAX_CHARGE_KW, 0) or 0)
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        if (kw <= 0 or soc is None or not self.active(MODULE_BATTERY) or not self.profile.ready
                or not self.coordinator.data or p.current is None):
            return
        cap, mx, mn = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MAX_SOC], self.settings[S_BATTERY_MIN_SOC]
        if cap <= 0:
            return
        floor, full = cap * mn / 100, cap * mx / 100
        spread = float(self.settings.get(S_BATTERY_MIN_SPREAD, 0.0))
        solar = self._solar_hours() or {}
        tz = self.tz
        local = now.astimezone(tz)
        loc = lambda sl: sl.start.astimezone(tz)  # noqa: E731
        today = [sl for sl in self.coordinator.data if loc(sl).date() == local.date() and sl.end > now]
        charge = set(p.battery_charge)
        night = [sl for sl in p.battery_charge if loc(sl).hour >= DAY_CYCLE_PEAK_H and sl.start > now]
        stop = min((sl.start for sl in night), default=None)            # aftenens netopladning
        cand = [sl for sl in today if DAY_CYCLE_FROM_H <= loc(sl).hour < DAY_CYCLE_PEAK_H + 2 and sl not in charge]
        cand = in_window(cand, tz, float(self.settings.get("battery_charge_from", 0)),
                         float(self.settings.get("battery_charge_to", 24)))
        if not cand:
            return
        first = cheapest_blocks(cand, BATTERY_MIN_BLOCK_H, BATTERY_MIN_BLOCK_H)
        if not first:
            return
        cost = sum(sl.price for sl in first) / len(first) / BATTERY_ROUND_TRIP
        peak = [sl for sl in today if loc(sl).hour >= DAY_CYCLE_PEAK_H and sl not in charge and sl.start > first[-1].end
                and (stop is None or sl.start < stop) and sl.price - cost >= spread]
        if not peak:
            return
        peak_start = min(sl.start for sl in peak)
        cand = [sl for sl in cand if sl.end <= peak_start]
        # Batteriet frem til toppen uden ladning fra nettet (huset trækker, solen lægger til)
        _, e_peak = simulate_battery(cap * soc / 100, self.profile, solar, local, peak_start.astimezone(tz),
                                     floor=floor, full=full)
        e_peak = max(e_peak, floor)
        need = 0.0
        for sl in peak:
            h = loc(sl).replace(minute=0, second=0, microsecond=0)
            frac = (sl.end - sl.start).total_seconds() / 3600
            need += max(self.profile.hours_for(h)[h.hour] - (solar.get(h) or 0.0), 0.0) * frac
        kwh = min(need / EFFICIENCY - (e_peak - floor), full - e_peak)
        if kwh < 1.0:            # under 1 kWh: ikke værd at starte en opladning for
            return
        hours = math.ceil(kwh / (kw * 0.95) * 4) / 4
        pick = cheapest_blocks(cand, hours, BATTERY_MIN_BLOCK_H, min_gap_h=BATTERY_MIN_GAP_H)
        if not pick:
            return
        avg_c = sum(sl.price for sl in pick) / len(pick)
        avg_p = sum(sl.price for sl in peak) / len(peak)
        if avg_p - avg_c / BATTERY_ROUND_TRIP < spread:
            return
        _, e_start = simulate_battery(cap * soc / 100, self.profile, solar, local, max(pick[0].start.astimezone(tz), local),
                                      floor=floor, full=full)
        target = min(max(e_start, floor) + kwh, full)
        p.battery_charge = sorted(set(p.battery_charge) | set(pick), key=lambda sl: sl.start)
        p.battery_discharge = [sl for sl in p.battery_discharge if sl not in pick]
        self.day_cycle = {"slots": set(pick), "target_kwh": target, "kwh": round(kwh, 1),
                          "need_at_peak": min(e_peak + kwh, full),
                          "start": pick[0].start, "end": pick[-1].end, "peak_from": peak_start,
                          "charge_price": round(avg_c, 3), "peak_price": round(avg_p, 3)}
        if p.current in self.day_cycle["slots"] and soc < mx:
            p.battery_action = ACTION_CHARGE

    @property
    def grid_buffer_kwh(self) -> float:
        """Ekstra kWh oven i nattens beregnede netopladning (indstilling, standard 1 kWh)."""
        try:
            return max(float(self.settings.get(S_BATTERY_GRID_BUFFER, 1.0) or 0.0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _charge_target(self, start: datetime, price: float) -> float:
        """Hvor mange kWh batteriet skal lades op til fra nettet i en ladeblok, der starter på `start`."""
        opt = self.__dict__.get("_opt_targets")
        if opt:
            for (a, b), target in opt.items():
                if a <= start < b:
                    return target                       # den samlede beregning (optimize)
        dc = self.__dict__.get("day_cycle")
        if dc and any(sl.start <= start < sl.end for sl in dc["slots"]):
            return dc["target_kwh"]                     # dagens ekstra opladning til aftenens top
        cap, mx = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MAX_SOC]
        full = cap * mx / 100
        n = self._night_need(start)
        if n is None or self.full_charge_hours:
            return full
        local = start.astimezone(self.tz)
        day = local if local.hour < SUN_END - 3 else local + timedelta(days=1)
        if self._sell_solar_better(day, price)[1]:
            return full
        return min(n["need_at_sun"] + self._load_until_sun(start, n["sun"]) / EFFICIENCY + self.grid_buffer_kwh, full)

    def _charge_end(self, t: datetime) -> datetime:
        """Slut på den sammenhængende ladeblok, som `t` ligger i (eller `t`, hvis der ikke lades)."""
        end = t
        for s in sorted(self.plan.battery_charge, key=lambda s: s.start):
            if s.start <= end < s.end or (s.start == end and end > t):
                end = s.end
        return end

    def _load_until_sun(self, t: datetime, sun: datetime) -> float:
        """Husets forbrug fra batteriet indtil solen: mens der lades fra nettet, kører huset på nettet
        (og batteriet står stille, når målet er nået), så forbruget tæller først fra ladeblokkens slutning."""
        start = self._charge_end(t) if self.has_hold else t
        local = start.astimezone(self.tz)
        return expected_load(self.profile, local, sun) if local < sun else 0.0

    def _plan_grid_charge(self, now: datetime) -> dict | None:
        """Næste opladning fra nettet: tidsrum, kWh, SoC og pris. None = ingen opladning planlagt."""
        p = self.plan
        later = sorted((s for s in p.battery_charge if s.end > now), key=lambda s: s.start)
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        cap = self.settings[S_BATTERY_CAPACITY]
        if not later or soc is None or cap <= 0:
            return None
        # Samme ladenat: tidsrum med højst 8 timers mellemrum og ingen afladning/salg imellem
        stops = sorted(s.start for s in p.battery_discharge if s.end > now)
        dc = self.__dict__.get("day_cycle")
        dcs = dc["slots"] if dc else set()
        block = [later[0]]
        for s in later[1:]:
            gap_end = s.start
            if (gap_end - block[-1].end) > timedelta(hours=8) or any(block[-1].end <= d < gap_end for d in stops) \
                    or (s in dcs) != (block[0] in dcs):      # dagens ekstra opladning er sin egen blok
                break
            block.append(s)
        start, end = max(block[0].start, now), block[-1].end
        hours = sum(((sl.end - max(sl.start, now)).total_seconds() for sl in block if sl.end > now), 0.0) / 3600
        periods, prev = 0, None
        for sl in block:
            periods += prev is None or sl.start != prev
            prev = sl.end
        price = sum(s.price for s in block) / len(block)
        floor, full = cap * self.settings[S_BATTERY_MIN_SOC] / 100, cap * self.settings[S_BATTERY_MAX_SOC] / 100
        e = cap * soc / 100
        lv = (self.__dict__.get("_opt_levels") or {}).get(block[0].start)
        if lv is not None and start > now:
            e = lv                                  # den samlede beregning ved, hvor batteriet står (gem og sol medregnet)
        elif start > now and self.profile.ready:    # batteriet dækker huset frem til ladeblokken
            solar = self._solar_hours() or {}
            _, e = simulate_battery(e, self.profile, solar, now.astimezone(self.tz), start.astimezone(self.tz),
                                    floor=floor, full=full)
            e = max(e, floor)
        target = self._charge_target(start, price)
        kwh = max(target - e, 0.0)
        kw = float(self.settings.get(S_BATTERY_MAX_CHARGE_KW, 0) or 0)
        if kw > 0:                                  # kan ikke lade mere, end tidsrummet giver plads til
            kwh = min(kwh, kw * 0.95 * hours)
        if kwh <= 0.05:
            return None
        # Morgendagens priser kendes ikke endnu: flere ladetimer i nat kan komme til
        data = getattr(self.coordinator, "data", None) or []
        last = max((sl.end for sl in data), default=end)
        next_morning = (end.astimezone(self.tz) + timedelta(hours=12)).replace(hour=6, minute=0, second=0, microsecond=0)
        if next_morning - end.astimezone(self.tz) > timedelta(hours=18):
            next_morning -= timedelta(days=1)
        in_dc = block[0] in dcs
        pending = last < next_morning and e + kwh < target - 0.3 and not in_dc
        return {"start": start.isoformat(), "end": end.isoformat(), "kwh": round(kwh, 1),
                "day_cycle": in_dc, "peak_from": dc["peak_from"].isoformat() if in_dc else None,
                "hours": round(hours, 2), "periods": periods, "prices_pending": pending,
                "soc_at_start": round(e / cap * 100), "target_soc": round((e + kwh) / cap * 100),
                "avg_price": round(price, 2), "cost": round(kwh * price, 2)}

    def _latched_need_hours(self, need_h: float | None) -> float | None:
        """Ladetiden må ikke svinge frem og tilbage i løbet af natten (fx 0,75 ↔ 1 time hvert halve minut, så
        ladeblokken skifter og batteriet slår lad/normal til og fra). Natten over bruges den største værdi,
        der er regnet ud; styringen stopper alligevel, når målet er nået. Nulstilles ved solopgang."""
        local = dt_util.now().astimezone(self.tz)
        night = (local if local.hour < SUN_START else local + timedelta(days=1)).date()
        memo = self.__dict__.get("_need_h_memo")
        if need_h is None:
            return None
        if local.hour >= SUN_START and local.hour < SUN_END - 3:
            self._need_h_memo = None                    # om dagen: ingen låsning
            return need_h
        if memo and memo[0] == night and need_h > 0:
            need_h = max(need_h, memo[1])
        self._need_h_memo = (night, need_h)
        return need_h

    def _grid_charge_hours(self, now: datetime, soc: float | None) -> float | None:
        """Timer der skal lades fra nettet i nat ved den indtastede maks. ladeeffekt. None = ingen ladeeffekt/data."""
        kw = float(self.settings.get(S_BATTERY_MAX_CHARGE_KW, 0) or 0)
        if kw <= 0 or soc is None:
            return None
        n = self._night_need(now)
        if n is None:
            return None
        cap = self.settings[S_BATTERY_CAPACITY]
        local = now.astimezone(self.tz)
        e = cap * soc / 100
        # Batteriet frem til natten (dækker huset, solen lægger til), derefter forbruget frem til solopgang
        midnight = n["sun"].replace(hour=0)
        if local < midnight:
            _, e = simulate_battery(e, self.profile, n["solar"], local, midnight, floor=n["floor"], full=n["full"])
            e = max(e, n["floor"])
            start = midnight
        else:
            start = local
        # Samme mål som planen og styringen (_charge_target / _decide_grid_charge): inkl. ladetab, højst fuldt
        target = min(n["need_at_sun"] + self._load_until_sun(start, n["sun"]) / EFFICIENCY + self.grid_buffer_kwh,
                     n["full"])
        need_kwh = target - e
        if need_kwh <= 0.05:
            return 0.0
        return math.ceil(need_kwh / (kw * 0.95) * 4) / 4

    def _decide_grid_charge(self, now: datetime) -> None:
        """Lad kun fra nettet, hvad solen ikke selv kan fylde.

        Er der en planlagt netopladning, og forventer solprognosen et overskud (efter husets forbrug),
        lades batteriet kun op til: laveste niveau + husets forbrug indtil solen giver overskud – eller
        så meget, at solen kan fylde resten. Kan det betale sig at sælge solstrømmen i stedet
        (salgspris midt på dagen > nettopladningens pris inkl. tab + margin), lades der fuldt.
        """
        self.grid_charge = None
        p = self.plan
        if p.current is None or p.battery_action != ACTION_CHARGE or p.current not in p.battery_charge:
            return
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        if soc is None:
            return
        dc = self.__dict__.get("day_cycle")
        if dc and p.current in dc["slots"]:
            # Dagens ekstra opladning: lad til det, aftenens top kræver
            cap, mx = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MAX_SOC]
            target = dc["target_kwh"]
            skip = cap * soc / 100 >= target - 0.05
            # Regn igen med den sol, der rent faktisk kommer i dag: når solen alene batteriet op på det,
            # aftenens top kræver, inden toppen starter? Så købes der ikke fra nettet midt på dagen (det kan
            # ellers fylde batteriet for tidligt, så solen bagefter sælges billigt).
            solar_covers = False
            solar = self._solar_hours()
            need_peak = dc.get("need_at_peak")
            peak_from = dc.get("peak_from")
            if not skip and solar and need_peak is not None and peak_from is not None and peak_from > now:
                mn = self.settings[S_BATTERY_MIN_SOC]
                _, e_peak = simulate_battery(cap * soc / 100, self.profile, solar, now.astimezone(self.tz),
                                             peak_from.astimezone(self.tz), floor=cap * mn / 100, full=cap * mx / 100)
                solar_covers = e_peak >= need_peak - 0.05
                skip = solar_covers
            self.grid_charge = {"target_kwh": round(target, 2),
                                "target_soc": round(min(target / cap * 100, mx), 0) if cap else None,
                                "charge_price": round(p.current.price, 3), "day_cycle": True, "skipping": skip,
                                "solar_covers": solar_covers}
            if skip:
                p.battery_action = ACTION_HOLD if self.has_hold else ACTION_NORMAL
            return
        fc = self.solar_forecast
        local = now.astimezone(self.tz)
        if local.hour < SUN_START:
            day, kwh, from_h = local, fc.get("today_kwh"), SUN_START
        elif local.hour < SUN_END - 3:
            day, kwh, from_h = local, fc.get("remaining_kwh"), local.hour
        else:
            day, kwh, from_h = local + timedelta(days=1), fc.get("tomorrow_kwh"), SUN_START
        if kwh is None:
            return
        # forsigtigt: prognosen rammer ofte for højt – og i dag justeret efter den faktiske sol
        kwh *= self.solar_factor_today if day.date() == local.date() else self.solar_caution
        surplus = solar_surplus(self.profile, day, kwh, from_h)
        sun_at = day.replace(hour=min(max(from_h, SUN_START) + 3, 23), minute=0, second=0, microsecond=0)
        until_sun = expected_load(self.profile, local, max(sun_at, local))
        cap, mx, mn = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MAX_SOC], self.settings[S_BATTERY_MIN_SOC]
        target = grid_charge_target(capacity=cap, min_soc=mn, max_soc=mx, surplus_kwh=surplus, until_sun_kwh=until_sun)
        n = self._night_need(now)
        if n is not None and local < n["sun"]:
            # Time for time: det batteriet skal have ved solopgang + husets forbrug indtil da
            target = min(n["need_at_sun"] + self._load_until_sun(now, n["sun"]) / EFFICIENCY + self.grid_buffer_kwh,
                         cap * mx / 100)
        # Er salg af solstrøm midt på dagen bedre end at bruge den selv? Så lades der fuldt fra nettet.
        sale_mid, sell_better = self._sell_solar_better(day, p.current.price)
        if sell_better or self.full_charge_hours:
            target = cap * mx / 100                  # sælg solen – eller "lad helt op" er slået til
        soc_kwh = cap * soc / 100
        skip = soc_kwh >= target - 0.05
        self.grid_charge = {
            "target_kwh": round(target, 2),
            "target_soc": round(min(target / cap * 100, mx), 0) if cap else None,
            "solar_forecast_kwh": round(kwh, 2),
            "solar_surplus_kwh": round(surplus, 2),
            "house_until_sun_kwh": round(until_sun, 2),
            "sale_midday": round(sale_mid, 3) if sale_mid is not None else None,
            "charge_price": round(p.current.price, 3),
            "sell_solar_better": sell_better,
            "skipping": skip,
        }
        if skip:
            # Målet er nået: stå stille resten af ladeblokken – ellers bruges strømmen i nattens billigste timer,
            # og opladningen starter igen, så snart niveauet falder lidt
            p.battery_action = ACTION_HOLD if self.has_hold else ACTION_NORMAL

    def _decide_sell_solar(self, now: datetime) -> None:
        """Sælg solstrømmen nu og lad batteriet billigt senere – hvis det betaler sig og huset er dækket.

        Betingelser (alle skal være opfyldt):
        * en tilstand/et script til "sælg solstrøm" er sat op,
        * batteriet har allerede reserven til huset frem til næste billige opladning,
        * salgsprisen nu er højere end prisen for at lade fra nettet senere (inkl. tab) + den valgte margin,
        * solcellerne producerer (hvis der er en effektsensor).
        """
        p, r = self.plan, self.reserve
        if not self.has_sell_solar or r is None or p.current is None or p.battery_action != ACTION_NORMAL:
            return
        later = [s for s in p.battery_charge if s.start > now]
        if not later:
            return
        charge_price = sum(s.price for s in later) / len(later)
        sale_now = p.current.sale
        margin = float(self.settings.get(S_SOLAR_SELL_MARGIN, 0.10))
        gain = sale_now - charge_price / EFFICIENCY
        solar_kw = self._power_kw(self.config.get(CONF_SOLAR_POWER)) if self.has(CONF_SOLAR_POWER) else None
        covered = r.sellable_kwh is not None and r.sellable_kwh > 0.05
        active = gain >= margin and covered and (solar_kw is None or solar_kw > 0.2)
        self.sell_solar = {
            "active": active,
            "sale_now": round(sale_now, 3),
            "charge_price_later": round(charge_price, 3),
            "gain_per_kwh": round(gain, 3),
            "margin": margin,
            "house_covered": covered,
        }
        if active:
            p.battery_action = ACTION_SELL_SOLAR

    def _optimize_battery(self, now: datetime) -> bool:
        """Samlet batteriplan (slået til med "Samlet beregning"): lad/gem/brug for hvert kvarter vælges samlet
        ud fra pris, forbrug, sol, tab og indstillinger (optimizer.py). Returnerer True, hvis planen er sat."""
        self._opt_targets = None
        self._opt_levels = None
        self.optimizer = None
        p = self.plan
        if not self.flags.get(F_BATTERY_OPTIMIZER) or self.full_charge_hours or p.current is None:
            return False
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        cap = float(self.settings[S_BATTERY_CAPACITY])
        if soc is None or cap <= 0 or not self.profile.ready:
            return False
        tz = self.tz
        data = sorted((s for s in (self.coordinator.data or []) if s.end > now), key=lambda s: s.start)
        run = []
        for s in data:
            if run and s.start != run[-1].end:
                break
            if s.start - now > timedelta(hours=48):
                break
            run.append(s)
        if len(run) < 4:
            return False
        solar = self._solar_hours() or {}
        win_from = float(self.settings.get("battery_charge_from", 0) or 0)
        win_to = float(self.settings.get("battery_charge_to", 24) or 24)
        allowed = {s.start for s in in_window(run, tz, win_from, win_to)}
        opt_slots = []
        for s in run:
            lt = s.start.astimezone(tz)
            hour = lt.replace(minute=0, second=0, microsecond=0)
            hrs = (s.end - max(s.start, now)).total_seconds() / 3600
            load = self.profile.hours_for(lt)[lt.hour] * hrs
            sun = solar.get(hour, 0.0) * hrs
            opt_slots.append(OptSlot(max(s.start, now), s.end, s.price, s.sale, load, sun, s.start in allowed))
        morning_buffer(opt_slots, tz, self.grid_buffer_kwh)
        mn, mx = float(self.settings[S_BATTERY_MIN_SOC]), float(self.settings[S_BATTERY_MAX_SOC])
        floor, full = cap * mn / 100, cap * mx / 100
        kw = float(self.settings.get(S_BATTERY_MAX_CHARGE_KW, 0) or 0)
        prev = self.__dict__.get("_opt_prev")
        try:
            res = optimize(opt_slots, cap * soc / 100, floor=floor, full=full, charge_kw=kw,
                           min_spread=float(self.settings.get(S_BATTERY_MIN_SPREAD, 0.0)),
                           can_hold=self.has_hold, prev_action=prev)
        except Exception:  # noqa: BLE001 – må aldrig stoppe styringen; de gamle regler tager over
            _LOGGER.exception("SmartHus: samlet batteriberegning fejlede – bruger de hidtidige regler")
            return False
        if res is None:
            return False
        past = [s for s in p.battery_charge if s.end <= now]
        charge = [s for s, a in zip(run, res.actions) if a == "charge"]
        hold = [s for s, a in zip(run, res.actions) if a == "hold"]
        p.battery_charge = past + charge
        p.battery_hold = hold
        p.battery_discharge = [s for s in p.battery_discharge if s not in charge and s not in hold]
        self.day_cycle = None
        # Lademål pr. ladeblok: batteriets niveau, når blokken slutter
        targets, i = {}, 0
        while i < len(run):
            if res.actions[i] != "charge":
                i += 1
                continue
            j = i
            while j + 1 < len(run) and res.actions[j + 1] == "charge":
                j += 1
            targets[(run[i].start, run[j].end)] = res.levels[j + 1]
            i = j + 1
        self._opt_targets = targets
        self._opt_levels = {sl.start: lv for sl, lv in zip(run, res.levels)}   # niveau ved starten af hvert kvarter
        first = res.actions[0]
        self._opt_prev = first
        if p.battery_action != ACTION_DISCHARGE:            # salg fra batteriet styres som før
            p.battery_action = {"charge": ACTION_CHARGE, "hold": ACTION_HOLD}.get(first, ACTION_NORMAL)
        nxt = targets[next(iter(targets))] if targets else None
        self.grid_charge = {"optimizer": True, "action": first, "skipping": False,
                            "target_kwh": round(nxt, 2) if nxt is not None else None,
                            "target_soc": round(nxt / cap * 100) if nxt is not None else None}
        self.battery_hold = {"active": first == "hold", "usable_kwh": round(max(soc - mn, 0) / 100 * cap, 2),
                             "covers_price_from": None, "optimizer": True}
        self.optimizer = {"cost": round(res.cost, 2), "slots": len(run),
                          "end_soc": round(res.levels[-1] / cap * 100, 1),
                          "grid_kwh": round(sum(res.grid_kwh), 2)}
        return True

    def _decide_hold(self, now: datetime, until: datetime) -> None:
        """Gem batteriet i billige timer, så strømmen rækker til de dyre timer før næste opladning."""
        p = self.plan
        if not self.has_hold or p.current is None:
            return
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        if soc is None:
            return
        cap, mn = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MIN_SOC]
        usable = max(soc - mn, 0.0) / 100 * cap
        local = now.astimezone(self.tz)
        data = self.coordinator.data or []
        # Frem til næste planlagte opladning – ellers til den sidste kendte pris (ikke "næste billige tidsrum",
        # for så kunne batteriet blive brugt op midt på dagen og mangle i aftenspidsen)
        nxt = [s.start for s in p.battery_charge if s.start > now]
        until = min(nxt) if nxt else max((s.end for s in data), default=until)
        slots = [s for s in data if s not in p.battery_charge]
        full = cap * self.settings[S_BATTERY_MAX_SOC] / 100
        hold, limit = plan_hold(slots, self.profile, local, until.astimezone(self.tz), usable,
                                previous=getattr(self, "_hold_prev", set()), solar=self._solar_hours(),
                                room_kwh=max(full - cap * soc / 100, 0.0))
        self._hold_prev = {s.start for s in hold}
        if p.battery_action == ACTION_HOLD and p.current in p.battery_charge:
            # Lademålet er nået midt i en ladeblok: resten af blokken står batteriet stille (ét tidsrum til inverteren)
            by = {s.start: s for s in p.battery_charge}
            blk, t = [], p.current.start
            while t in by:
                blk.append(by[t])
                t = by[t].end
            hold = blk + [s for s in hold if s.start not in {x.start for x in blk}]
        p.battery_hold = sorted(hold, key=lambda s: s.start)
        self.battery_hold = {"active": p.current in hold, "usable_kwh": round(usable, 2),
                             "covers_price_from": round(limit, 3) if limit is not None else None}
        if p.battery_action == ACTION_NORMAL and p.current in hold:
            p.battery_action = ACTION_HOLD

    def _track_sun(self) -> None:
        """Læg solcellernes faktiske effekt og prognosens effekt for samme tidspunkt sammen (til SunTracker)."""
        if not self._forecast_wh or not self.has(CONF_SOLAR_POWER):
            return
        now = dt_util.now().astimezone(self.tz)
        raw = solar_by_hour(self._forecast_wh, self.tz, self._forecast_key_is_end)
        hour = now.replace(minute=0, second=0, microsecond=0)
        self.sun.day_kwh = sum(v * self.solar_cal.factor(h) for h, v in raw.items() if h.date() == now.date())
        self.sun.update(now, self.solar_kw, raw.get(hour, 0.0) * self.solar_cal.factor(hour))

    @property
    def solar_factor_today(self) -> float:
        """Andel af prognosen, der regnes med resten af i dag (lavere, hvis solen har svigtet indtil nu)."""
        sun = getattr(self, "sun", None)
        return sun.factor(self.solar_caution) if sun else self.solar_caution

    @property
    def solar_caution(self) -> float:
        """Den andel af solprognosen, batteriet planlægges ud fra (indstilling)."""
        return min(max(float(self.settings.get(S_SOLAR_FORECAST_PCT, 70.0)), 0.0), 100.0) / 100

    def _solar_hours(self) -> dict | None:
        """Solprognosen som kWh pr. lokal time – forsigtigt, og resten af i dag justeret efter den faktiske sol."""
        if not self._forecast_wh:
            return None
        today = dt_util.now().astimezone(self.tz).date()
        f_today = self.solar_factor_today
        return {h: kwh * self.solar_cal.factor(h) * (f_today if h.date() == today else self.solar_caution)
                for h, kwh in solar_by_hour(self._forecast_wh, self.tz, self._forecast_key_is_end).items()}

    def _update_battery_why(self, now: datetime) -> None:
        try:
            self.battery_why = self._battery_why(now)
        except Exception:  # noqa: BLE001 – kun forklaring; må aldrig stoppe styringen
            _LOGGER.debug("SmartHus: kunne ikke lave batteriets forklaring", exc_info=True)
            self.battery_why = None

    def _forecast_battery(self, now: datetime) -> None:
        """Skøn over batteriet resten af i dag og i morgen: brug i huset, besparelse og salg."""
        p = self.plan
        self.planned_charge = self._plan_grid_charge(now)
        self._update_battery_why(now)          # foreløbig – regnes igen, når brug/gem-tidsrummene kendes
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        if soc is None or p.current is None:
            return
        cap = self.settings[S_BATTERY_CAPACITY]
        t = self.savings.data.get("battery_trade", {})
        if t.get("stock_kwh", 0) > 0.1:
            stock = (t.get("stock_grid", 0) + t.get("stock_solar", 0)) / t["stock_kwh"]
        else:
            stock = (sum(s.price for s in p.battery_charge) / len(p.battery_charge)) if p.battery_charge else p.current.price
        by_start = {s.start: s.price for s in p.battery_charge}
        f = forecast_battery(self.coordinator.data or [], self.profile, now.astimezone(self.tz),
                             battery_kwh=cap * soc / 100, floor_kwh=cap * self.settings[S_BATTERY_MIN_SOC] / 100,
                             full_kwh=cap * self.settings[S_BATTERY_MAX_SOC] / 100, charge=p.battery_charge,
                             hold=p.battery_hold, sell=p.battery_discharge, stock_price=stock, can_hold=self.has_hold,
                             solar=self._solar_hours(),
                             charge_target=lambda t: self._charge_target(t, by_start[t]))
        p.battery_use = f["use"]
        # Vis kun salg, hvor der forventes strøm over reserven (den igangværende time styres af reserven selv)
        will_sell = {s.start for s in f.get("sell", [])}
        p.battery_discharge = [s for s in p.battery_discharge if s.start in will_sell or s is p.current]
        p.battery_solar = f["solar"]
        p.solar_cover = f["sun_cover"]
        self._update_battery_why(now)          # nu med de tidsrum, batteriet bruges i (hvad det gemmes til)
        # Vis også gem-tidsrummene efter næste opladning (de nuværende styrer stadig inverteren)
        now_hold = {s.start for s in p.battery_hold}
        p.battery_hold = p.battery_hold + [s for s in f["hold"] if s.start not in now_hold]
        today = now.astimezone(self.tz).date()
        keys = sorted(f["days"])
        # Hele dagen: det, der allerede er sket i dag (målt), plus skønnet for resten af dagen
        rest = f["days"].get(today.isoformat()) or {}
        done = self.savings.trade("today") if hasattr(self.savings, "trade") else {}
        so_far = {
            "house_kwh": done.get("house_kwh", 0.0), "house_value": done.get("house_value", 0.0),
            "house_cost": done.get("house_cost", 0.0), "sell_kwh": done.get("sold_kwh", 0.0),
            "sell_income": done.get("income", 0.0),
            "sell_cost": done.get("cost_grid", 0.0) + done.get("cost_solar", 0.0),
        }
        so_far["house_saving"] = so_far["house_value"] - so_far["house_cost"]
        so_far["sell_gain"] = so_far["sell_income"] - so_far["sell_cost"]
        whole = {k: round(so_far.get(k, 0.0) + rest.get(k, 0.0), 2) for k in so_far}
        whole["solar_kwh"] = round(rest.get("solar_kwh", 0.0), 2)        # skøn: solens opladning resten af dagen
        self.battery_forecast = {
            "today": whole,
            "today_so_far": {k: round(v, 2) for k, v in so_far.items()},
            "today_rest": rest or None,
            "tomorrow": f["days"].get((today + timedelta(days=1)).isoformat()),
            "cost_per_kwh": round(stock / 0.9, 3),
            "best_sale": round(max((s.sale for s in (self.coordinator.data or []) if s.end > now), default=0.0), 3),
            "solar_factor": round(self.solar_factor_today, 2),
            "solar_ratio_today": round(self.sun.ratio, 2) if getattr(self, "sun", None) and self.sun.ratio is not None else None,
            "solar_measured_kwh": round(self.sun.measured, 2) if getattr(self, "sun", None) else None,
            "solar_forecast_so_far_kwh": round(self.sun.forecast, 2) if getattr(self, "sun", None) else None,
            "days": keys,
        }

    async def async_stop(self) -> None:
        for name in ("_unsub_learn", "_unsub_learn_first", "_unsub_temps", "_unsub_sun_learn", "_unsub_sun_learn_first", "_unsub_energy", "_unsub_energy_first"):
            unsub = getattr(self, name, None)
            if unsub:
                unsub()
                setattr(self, name, None)
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_prices:
            self._unsub_prices()
            self._unsub_prices = None
        if getattr(self, "_unsub_sl", None):
            self._unsub_sl()
            self._unsub_sl = None
        for m in list(self._restore_pending):
            self._cancel_restore(m)
        if getattr(self, "_unsub_heal", None):
            self._unsub_heal()
            self._unsub_heal = None
        await self._store.async_save(self.savings.data)

    @callback
    def _on_prices(self) -> None:
        self.recalculate()

    # --- Hjælpere ----------------------------------------------------------
    def has(self, key: str) -> bool:
        # Varme tæller som opsat med en termostat – eller med scripts til en luft-til-luft varmepumpe
        if key == CONF_HEATING_CLIMATE:
            return self.has_heating_control
        # Batteri og eksport tæller som opsat, hvis der enten er valgt en entitet eller scripts
        if key == CONF_BATTERY_MODE_ENTITY:
            return self.has_battery_control
        if key == CONF_SOLAR_CURTAIL:
            return self.has_export_control
        if key == "battery_sell_solar":
            return self.has_sell_solar
        if key == CONF_GRID_POWER:     # én sensor, eller køb + salg som to
            return bool(self.config.get(CONF_GRID_POWER)) or bool(
                self.config.get(CONF_GRID_IMPORT_POWER) and self.config.get(CONF_GRID_EXPORT_POWER))
        return bool(self.config.get(key))

    @property
    def has_battery_control(self) -> bool:
        return (bool(self.config.get(CONF_BATTERY_MODE_ENTITY)) or bool(self.config.get(CONF_HUAWEI_DEVICE))
                or bool(self.config.get(CONF_GROWATT_DEVICE))
                or bool(self.config.get(CONF_BATTERY_ACTIONS)) or any(self.config.get(k) for k in BATTERY_SCRIPTS))

    @property
    def has_sell_solar(self) -> bool:
        """Kan batteriet sættes til at sælge solstrømmen i stedet for at lade? (valgfri tilstand/script)"""
        return bool(self.config.get(CONF_BATTERY_SCRIPT_SELL_SOLAR)) or (
            bool(self.config.get(CONF_BATTERY_MODE_ENTITY)) and bool(self.config.get(CONF_BATTERY_OPT_SELL_SOLAR)))

    @property
    def has_hold(self) -> bool:
        """Kan batteriet stå stille (ikke aflade, men gerne lade af solen)?"""
        if self.config.get(CONF_BATTERY_SCRIPT_HOLD):
            return True
        if self.config.get(CONF_BATTERY_ACTIONS):
            return bool(self.config[CONF_BATTERY_ACTIONS].get(ACTION_HOLD))
        if self.config.get(CONF_GROWATT_DEVICE):
            return (self.config.get(CONF_GROWATT_TYPE) or gw.TYPE_SPH) == gw.TYPE_SPH
        if self.config.get(CONF_HUAWEI_DEVICE):
            return self._huawei_discharge_entity() is not None
        return bool(self.config.get(CONF_BATTERY_MODE_ENTITY)) and bool(self.config.get(CONF_BATTERY_OPT_HOLD))

    @property
    def has_export_control(self) -> bool:
        return bool(self.config.get(CONF_SOLAR_CURTAIL)) or any(self.config.get(k) for k in EXPORT_SCRIPTS)

    def active(self, module: str) -> bool:
        return self.enabled[MODULE_MASTER] and self.enabled.get(module, False)

    def car(self, cid: str) -> dict | None:
        return next((c for c in self.cars if c["id"] == cid), None)

    def _float_state(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
            return None
        try:
            return float(st.state)
        except ValueError:
            return None

    def _energy_kwh(self, key: str) -> float | None:
        return self._energy_entity_kwh(self.config.get(key))

    def _energy_entity_kwh(self, entity_id: str | None) -> float | None:
        val = self._float_state(entity_id)
        if val is None:
            return None
        unit = (self.hass.states.get(entity_id).attributes.get("unit_of_measurement") or "kWh").lower()
        return val * _UNITS.get(unit, 1.0)

    @callback
    def recalculate(self) -> None:
        """Byg planen på ny og opdatér alle entiteter."""
        slots = self.coordinator.data or []
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        self._update_connected()
        cars = [
            CarInput(car["id"], car.get("name") or "Elbil",
                     soc=self.car_soc(car), force=self.ev_force.get(car["id"], False),
                     connected=self.connected.get(car["id"], True) and not self._soc_pending(car),
                     charged_h=self._charged_hours(car["id"]),
                     extra_h=self._extra_hours(car),
                     was_on_slot=self._ev_on_slot(car["id"]),
                     **self._ready_input(car["id"]))
            for car in self.cars
        ]
        settings = {**self.settings, "heating_auto": bool(self.flags.get(F_HEATING_AUTO, False)),
                    "battery_sell": bool(self.flags.get(F_BATTERY_SELL, True))}
        full_h = self.full_charge_hours
        if full_h:
            settings[S_BATTERY_CHARGE_HOURS] = full_h      # lad helt op: timerne regnes ud fra ladeeffekten
            settings["battery_full_charge"] = True
        else:
            need_h = self._latched_need_hours(self._grid_charge_hours(dt_util.utcnow(), soc))
            if need_h is not None:
                # Kun de timer, der skal til for at nå målet ved den indtastede ladeeffekt ("Timer at lade" er loftet)
                settings[S_BATTERY_CHARGE_HOURS] = min(settings[S_BATTERY_CHARGE_HOURS], need_h)
        # Når batteriet har nået højeste niveau, startes der først opladning igen, når det er faldet lidt
        mx = float(settings["battery_max_soc"])
        if soc is None or soc < mx - BATTERY_RECHARGE_MARGIN:
            self._battery_full = False
        elif soc >= mx:
            self._battery_full = True
        self.plan = build_plan(slots, dt_util.utcnow(), self.tz, settings, soc=soc, cars=cars,
                               battery_full=self.__dict__.get("_battery_full", False))
        if "_temps" in self.__dict__:
            self._apply_temp_adjust()
        self._drop_solar_charge(dt_util.utcnow())
        self.day_cycle = None
        try:
            self._plan_day_cycle(dt_util.utcnow())
        except Exception:  # noqa: BLE001 – en fejl her må aldrig stoppe resten af styringen
            _LOGGER.exception("SmartHus: kunne ikke planlægge dagens ekstra opladning")
        self._update_hotwater_demand()
        now = dt_util.utcnow()
        for cid, ep in self.plan.evs.items():
            past = self._ev_past.setdefault(cid, set())
            past.update(sl.start for sl in ep.slots if sl.start <= now)
            self._ev_past[cid] = {t for t in past if now - t < timedelta(hours=36)}
        for cid, ep in self.plan.evs.items():
            if self.ev_force.get(cid) and ep.target_reached:
                self.ev_force[cid] = False  # "Lad nu" slukker selv ved mål-SoC
        self._update_reserve()
        self._remember_past(now)
        if self.has(CONF_HEATING_CLIMATE):
            self._update_heating_outdoor()   # så kortet viser pause/aktiv med det samme
        async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))

    async def _async_tick(self, now: datetime) -> None:
        if time.monotonic() - self._forecast_fetched > 900 or not self._forecast_fetched:
            self._forecast_fetched = time.monotonic()
            await self._async_refresh_forecast()
        if self.active(MODULE_HOTWATER):
            await self._async_sample_hotwater()
            self._update_surplus()
        self._log_ev_charging()
        self._track_soc()
        self._track_fault()
        self._track_sun()
        if getattr(self.plan, "solar_curtail", False):
            self.solar_cal.mark_curtailed(dt_util.now().astimezone(self.tz))
        self.recalculate()
        await self.async_apply()
        await self._async_sync_reserve()
        self._update_savings()

    async def _async_sync_reserve(self) -> None:
        """Inverterens egen nedre grænse følger "Laveste niveau" – i normal drift er det kun den, der stopper batteriet."""
        entity_id = self.config.get(CONF_BATTERY_RESERVE)
        st = self.hass.states.get(entity_id) if entity_id else None
        current = self._float_state(entity_id) if st is not None else None
        if current is None:
            return
        wanted = float(self.settings[S_BATTERY_MIN_SOC])
        wanted = min(max(wanted, st.attributes.get("min", wanted)), st.attributes.get("max", wanted))
        if abs(current - wanted) >= 0.5:
            await self._call("number", "set_value", {"entity_id": entity_id, "value": wanted})

    # --- Hvornår skal bilen lade -------------------------------------------
    # next: frem til næste "klar kl." · today: resten af i dag · tomorrow: i morgen 00–24 ·
    # both: nu til og med i morgen · horizon: billigste i sigte (alle kendte priser).
    # Dagene gemmes som faste datoer, så perioden ikke flytter sig, når døgnet skifter eller nye
    # priser kommer. Når perioden er slut, går valget selv tilbage til "next".
    def _midnight(self, d) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=self.tz)

    def ready_mode(self, cid: str) -> tuple[str, object]:
        mode, d = self.ev_ready_mode.get(cid, ("next", None))
        if mode in ("today", "tomorrow", "both") and d is not None:
            if self._midnight(d) + timedelta(days=1) <= dt_util.utcnow():
                self.ev_ready_mode[cid] = ("next", None)      # perioden er slut: klar kl. igen
                return "next", None
        return mode, d

    def _ready_input(self, cid: str) -> dict:
        mode, d = self.ready_mode(cid)
        out: dict = {"horizon": mode == "horizon"}
        if mode in ("today", "tomorrow", "both") and d is not None:
            out["deadline"] = self._midnight(d) + timedelta(days=1)   # til midnat den sidste dag
            if mode == "tomorrow":
                out["earliest"] = self._midnight(d)
        return out

    def ready_at(self, cid: str) -> datetime:
        inp = self._ready_input(cid)
        if inp.get("deadline") and inp["deadline"] > dt_util.utcnow():
            return inp["deadline"]
        return car_ready(dt_util.utcnow(), self.tz, int(car_settings(self.settings, cid)["ready_hour"]),
                         None, inp["horizon"], self.coordinator.data or [])

    def set_ready_mode(self, cid: str, mode: str, day=None) -> None:
        self.ev_ready_mode[cid] = (mode, day)
        self._refresh_manual_expiry(cid)
        self.hold_ev(cid)
        self.recalculate()

    def hold_ev(self, cid: str) -> None:
        """Start ikke laderen de næste sekunder (brugeren er i gang med at ændre indstillinger)."""
        self._ev_hold[cid] = time.monotonic() + EV_CHANGE_GRACE_SEC

    def ev_on_hold(self, cid: str) -> bool:
        return self._ev_hold.get(cid, 0.0) > time.monotonic()

    def _ready_key(self, cid: str) -> str:
        if self.ready_mode(cid)[0] == "horizon":
            return f"horizon:{dt_util.now().date().isoformat()}"   # uden SoC: nyt døgn, nye timer
        return self.ready_at(cid).isoformat()

    def _charged_hours(self, cid: str) -> float:
        """Timer der er ladet frem mod bilens næste klar-tidspunkt (bruges uden SoC-sensor)."""
        key, hours = self._ev_charged.get(cid, ("", 0.0))
        if self._ev_full_key.get(cid) == self._ready_key(cid):
            return 1000.0                       # bilen er holdt op med at lade selv – den er fuld
        return hours if key == self._ready_key(cid) else 0.0

    # --- Biler uden SoC-sensor ----------------------------------------------
    def soc_sensor_ok(self, car: dict | None) -> bool:
        """Er der valgt en SoC-sensor, og giver den et tal lige nu? Ellers bruges det indtastede batteriniveau."""
        return self.has_soc_sensor(car) and self._float_state(car.get("soc")) is not None

    def has_soc_sensor(self, car: dict | None) -> bool:
        return bool(car and car.get("soc"))

    def car_stops_itself(self, cid: str) -> bool:
        """Brugeren har sat lademålet i bilen – SmartHus lader lidt længere og lader bilen stoppe selv."""
        return bool(self.flags.get(f"{cid}_car_target", False)) and not self.has_soc_sensor(self.car(cid))

    def _extra_hours(self, car: dict) -> float:
        cid = car["id"]
        if not self.car_stops_itself(cid):
            return 0.0
        extra = float(car_settings(self.settings, cid)["extra_hours"])
        st = self.manual_soc.get(cid)
        if st is not None:
            return 0.0 if st.get("done") else max(extra - st.get("over_h", 0.0), 0.0)
        return extra

    def _manual_expiry(self, cid: str) -> datetime:
        if self.ready_mode(cid)[0] == "horizon":
            return dt_util.utcnow() + timedelta(hours=24)
        return self.ready_at(cid)

    def manual_soc_value(self, cid: str) -> float | None:
        """Skønnet batteriniveau ud fra det indtastede tal og den energi, der er ladet siden."""
        st = self.manual_soc.get(cid)
        if st is None:
            return None
        cs = car_settings(self.settings, cid)
        est = st["base"] + st.get("kwh", 0.0) * CHARGE_EFFICIENCY / max(cs["battery_capacity"], 1.0) * 100
        if st.get("done"):
            est = max(est, cs["target_soc"])
        return round(min(est, 100.0), 1)

    def set_manual_soc(self, cid: str, value: float | None) -> None:
        if value is None:
            self.manual_soc.pop(cid, None)
        else:
            self.manual_soc[cid] = {"base": float(value), "kwh": 0.0, "over_h": 0.0, "done": False,
                                    "expires": self._manual_expiry(cid).isoformat()}
        self._ev_idle.pop(cid, None)
        self.hold_ev(cid)
        self.recalculate()

    def _refresh_manual_expiry(self, cid: str) -> None:
        """Ændres klar-tidspunktet, gælder det indtastede batteriniveau frem til det nye tidspunkt."""
        st = self.manual_soc.get(cid)
        if st is not None:
            st["expires"] = self._manual_expiry(cid).isoformat()

    def _track_manual(self, car: dict, now: datetime) -> None:
        cid = car["id"]
        st = self.manual_soc.get(cid)
        charger_on = self._charger_on(car)
        plugged = self.connected.get(cid, True)
        kw = self._power_kw(car.get("power")) if car.get("power") else None
        plug_known = self.connected_source.get(cid) == "sensor"
        # Bil uden SoC, men med kabelsensor: følger kablet i stedet for klar-tidspunktet
        follow_plug = plug_known and not self.has_soc_sensor(car)
        if st is not None:
            try:
                exp = datetime.fromisoformat(st.get("expires") or "")
            except ValueError:
                exp = None
            if not plugged and plug_known:
                self.manual_soc.pop(cid, None)   # bilen er kørt
                st = None
            elif exp is not None and now >= exp:
                if follow_plug:
                    st["expires"] = self._manual_expiry(cid).isoformat()   # står stadig i laderen: behold skønnet
                else:
                    self.manual_soc.pop(cid, None)   # forældet: klar-tidspunktet er gået
                    st = None
        if follow_plug:
            # Bilen er lige sat til, og ingen kender niveauet: regn med tomt batteri (kan rettes på kortet)
            seen = self.__dict__.setdefault("_plug_seen", {})
            if plugged and not seen.get(cid) and st is None:
                st = self.manual_soc[cid] = {"base": 0.0, "kwh": 0.0, "over_h": 0.0, "done": False,
                                             "expires": self._manual_expiry(cid).isoformat()}
            seen[cid] = plugged
        # Ladet energi siden indtastningen (med effektmåler, ellers skønnet ud fra ladeeffekten)
        if st is not None and plugged and charger_on:
            cs = car_settings(self.settings, cid)
            step_kw = kw if kw is not None else cs["charge_power"]
            if step_kw > 0.3:
                st["kwh"] = st.get("kwh", 0.0) + step_kw * TICK_SECONDS / 3600
            if (self.manual_soc_value(cid) or 0) >= cs["target_soc"]:
                st["over_h"] = st.get("over_h", 0.0) + TICK_SECONDS / 3600
        # Bilen stopper selv: laderen er tændt, men bilen har ikke trukket strøm i et kvarter → fuld
        if not (self.car_stops_itself(cid) and kw is not None and charger_on and plugged):
            self._ev_idle.pop(cid, None)
            return
        if kw >= 0.3:
            self._ev_idle.pop(cid, None)
            return
        since = self._ev_idle.setdefault(cid, time.monotonic())
        if time.monotonic() - since >= EV_FULL_IDLE_SEC:
            _LOGGER.info("SmartHus: %s trækker ikke strøm længere – bilen har nået sit lademål", car.get("name"))
            if st is not None:
                st["done"] = True
            else:
                self._ev_full_key[cid] = self._ready_key(cid)
            self._ev_idle.pop(cid, None)

    def _log_ev_charging(self) -> None:
        """Tæl ladetid pr. klar-tidspunkt, så en bil uden SoC ikke lades de samme timer to gange."""
        for car in self.cars:
            cid = car["id"]
            key = self._ready_key(cid)
            last_key, hours = self._ev_charged.get(cid, (key, 0.0))
            if last_key != key:
                hours = 0.0
            ep = self.plan.evs.get(cid)
            if ep and ep.on and self._charger_on(car):
                hours += TICK_SECONDS / 3600
            self._ev_charged[cid] = (key, hours)

    def _update_savings(self) -> None:
        readings: dict[str, float | None] = {}
        for car in self.cars:
            if car.get("energy"):
                key = "ev" if car["id"] == "ev" else f"ev:{car['id']}"
                readings[key] = self._energy_entity_kwh(car["energy"])
        mapping = {
            "heatpump": CONF_HEATPUMP_ENERGY,
            "hotwater": CONF_HOTWATER_ENERGY,
            "battery_charge": CONF_BATTERY_CHARGE_ENERGY,
            "battery_discharge": CONF_BATTERY_DISCHARGE_ENERGY,
            "solar": CONF_SOLAR_ENERGY,
            "export": CONF_EXPORT_ENERGY,
            "import": CONF_IMPORT_ENERGY,
        }
        for name, key in mapping.items():
            if self.has(key):
                readings[name] = self._energy_kwh(key)
        if self._shared_heat_meter:
            readings.update(self._split_heat_meter(readings.get("heatpump")))
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        bat_kwh = soc / 100 * self.settings[S_BATTERY_CAPACITY] if soc is not None else None
        action = self.plan.battery_action if self.active(MODULE_BATTERY) else None
        full = soc is not None and soc >= self.settings[S_BATTERY_MAX_SOC] - BATTERY_RECHARGE_MARGIN - 1
        self.savings.update(dt_util.now(), self.plan.current, self.plan.day_avg, readings,
                            battery_action=action, battery_kwh=bat_kwh, battery_full=full,
                            full_charge=bool(self.flags.get(F_BATTERY_FULL_CHARGE)))
        # async_delay_save skubbes 60 s frem ved hvert kald (hvert 30. sekund) og ville aldrig nå at
        # skrive – kun ved en pæn nedlukning. Derfor skrives der fast hvert 5. minut, og delay_save
        # sørger for den sidste skrivning, når Home Assistant lukker ned.
        self._store.async_delay_save(lambda: self.savings.data, 60)
        if time.monotonic() - self._savings_saved >= SAVINGS_SAVE_SEC:
            self._savings_saved = time.monotonic()
            self.hass.async_create_task(self._store.async_save(self.savings.data))

    @property
    def _shared_heat_meter(self) -> bool:
        """Varme og varmt vand bruger samme energimåler (fx én varmepumpe til begge)."""
        hp, hw = self.config.get(CONF_HEATPUMP_ENERGY), self.config.get(CONF_HOTWATER_ENERGY)
        return bool(hp) and hp == hw

    @property
    def _heating_water_now(self) -> bool:
        """Varmer SmartHus vand lige nu (billig periode eller solcelleoverskud)?"""
        return (self.active(MODULE_HOTWATER) and bool(self.plan.hotwater_on or self.hotwater_surplus)
                and not self.hotwater_full)

    def _split_heat_meter(self, total: float | None) -> dict[str, float]:
        """Fordel en fælles måler: forbruget tæller som varmt vand, mens SmartHus varmer vand,
        ellers som varme. Så tælles hver kWh kun én gang."""
        out = {"heatpump": self._heat_cum["heatpump"], "hotwater": self._heat_cum["hotwater"]}
        if total is None:
            return out
        last, self._heat_last = self._heat_last, total
        if last is not None and 0 <= total - last <= 5:
            key = "hotwater" if self._heating_water_now else "heatpump"
            self._heat_cum[key] += total - last
            out[key] = self._heat_cum[key]
        return out

    # --- Styring -----------------------------------------------------------
    def _recently_sent(self, entity_id: str) -> bool:
        last = self._last_cmd.get(entity_id)
        return last is not None and time.monotonic() - last < MIN_COMMAND_INTERVAL

    def _rate_ok(self, entity_id: str) -> bool:
        last = self._last_cmd.get(entity_id)
        # Første kommando til en enhed sendes altid (også lige efter genstart)
        return last is None or time.monotonic() - last >= MIN_COMMAND_INTERVAL

    async def _call(self, domain: str, service: str, data: dict) -> None:
        entity_id = data["entity_id"]
        self._last_cmd[entity_id] = time.monotonic()
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as err:  # noqa: BLE001 – enhedsfejl må ikke stoppe styringen
            _LOGGER.warning("SmartHus kunne ikke styre %s (%s.%s): %s", entity_id, domain, service, err)

    async def _apply_ev_manual(self, car: dict, ep, force: bool) -> None:
        """Styringen af bilen er slået fra: "Lad nu" virker stadig, og er "Lad med solcelleoverskud" slået til,
        lades der kun med overskuddet (ikke efter prisplanen). Når det slutter, slukkes laderen igen – normal
        drift er slukket."""
        cid = car["id"]
        solar_manual = self.__dict__.setdefault("_ev_solar_manual", {})
        st = self.ev_solar.setdefault(cid, EvSolarState())
        if self.ev_force.get(cid) and ep.connected and not ep.target_reached:
            self._ev_manual[cid] = True
            st.reset()
            solar_manual.pop(cid, None)
            await self._set_charger(car, True, force)
            if self.has_current_control(car):
                await self._apply_current(car, self.full_current(car), force)
            return
        was_on = self._ev_manual.pop(cid, False) | solar_manual.pop(cid, False)
        solar_on = (self.flags.get(f"{cid}_solar", False) and self.has_current_control(car)
                    and self.export_kw is not None and ep.connected and not ep.target_reached)
        other = next((c for c, s in self.ev_solar.items() if s.active and c != cid), None)
        if solar_on and other is None:
            cs = car_settings(self.settings, cid)
            if not force:   # én reguleringsrunde pr. 30 sekunder
                regulate_ev(st, self.export_kw, self.solar_kw, int(cs["solar_min_a"]), int(cs["solar_max_a"]),
                            cs["solar_start_kw"])
            if st.active:
                solar_manual[cid] = True
                await self._set_charger(car, True, force)
                await self._apply_current(car, int(st.current), force)
                return
        else:
            st.reset()
        if was_on:
            await self._set_charger(car, False, True)

    PAST_KINDS = ("battery_use", "battery_hold", "battery_solar", "solar_cover")

    def _remember_past(self, now: datetime) -> None:
        for kind in self.PAST_KINDS:
            past = self._plan_past.setdefault(kind, {})
            for sl in getattr(self.plan, kind, []) or []:
                if sl.start <= now:
                    past[sl.start] = sl
            self._plan_past[kind] = {t: sl for t, sl in past.items() if now - t < timedelta(hours=36)}
        # Lade/aflade: husk kun de kvarterer, hvor batteriet faktisk blev sat til det (planen har hele døgnets
        # billigste timer – også dem, der blev sprunget over, fordi målet allerede var nået)
        p = self.plan
        for kind, action in self.DONE_KINDS.items():
            done = self.__dict__.setdefault("_plan_done", {}).setdefault(kind, {})
            if p.current is not None and p.battery_action == action and p.current.start <= now:
                done[p.current.start] = p.current
            self._plan_done[kind] = {t: sl for t, sl in done.items() if now - t < timedelta(hours=36)}

    DONE_KINDS = {"battery_charge": "charge", "battery_discharge": "discharge"}
    hold_sun = False
    ev_protect = False
    ev_fault: dict = {}
    _hold_sun_since: float | None = None

    def done_shown(self, kind: str, slots: list) -> list:
        """Til grafen: de kommende planlagte kvarterer + dem, batteriet faktisk har ladet/afladet i."""
        now = dt_util.utcnow()
        done = getattr(self, "_plan_done", {}).get(kind, {})
        ahead = [sl for sl in slots if sl.start > now]
        starts = {sl.start for sl in ahead}
        return ahead + [sl for t, sl in done.items() if t not in starts]

    def slots_shown(self, kind: str, slots: list) -> list:
        """Planens kvarterer + dem, der allerede er gået i gang (til grafen og periodelisten)."""
        past = self._plan_past.get(kind)
        if not past:
            return slots
        now_starts = {sl.start for sl in slots}
        return list(slots) + [sl for t, sl in past.items() if t not in now_starts]

    def ev_slots_shown(self, cid: str) -> list[str]:
        """Elbilens kvarterer til grafen: det der var planlagt (også overståede) + den kommende plan."""
        ep = self.plan.evs.get(cid)
        starts = set(self._ev_past.get(cid, set())) | {sl.start for sl in (ep.slots if ep else [])}
        return [t.isoformat() for t in sorted(starts)]

    @callback
    def _on_stromligning(self, _event) -> None:
        # Skal køre i Home Assistants event-loop (en almindelig lambda køres i en tråd → fejl)
        self.coordinator.async_refresh_external()

    async def _ev_verify_off(self, car: dict, on: bool) -> None:
        """Laderen er slukket i Home Assistant, men bilen lader stadig: sluk-kommandoen er ikke
        nået frem (fx Easee-skyen). Send den igen efter 2 minutter."""
        cid, entity_id = car["id"], car["charger"]
        st = self.hass.states.get(entity_id)
        kw = self._power_kw(car.get("power")) if car.get("power") else None
        if on or kw is None or kw < EV_STRAY_KW or st is None or self._charger_on(car):
            self._ev_stray[cid] = 0
            return
        self._ev_stray[cid] = self._ev_stray.get(cid, 0) + 1
        if self._ev_stray[cid] >= EV_STRAY_TICKS and self._rate_ok(entity_id):
            self._ev_stray[cid] = 0
            _LOGGER.warning("SmartHus: %s er slukket, men der lades stadig med %.1f kW – sender sluk igen",
                            entity_id, kw)
            domain = entity_id.split(".")[0]
            if domain in ("select", "input_select"):
                await self._call(domain, "select_option", {"entity_id": entity_id, "option": car.get("charger_off")})
            else:
                await self._call(domain if domain in ("switch", "input_boolean") else "homeassistant",
                                 "turn_off", {"entity_id": entity_id})
            if car.get("easee_device"):
                # Easee: sæt også opladningen på pause direkte (kontakten alene virker ikke altid)
                try:
                    await self.hass.services.async_call("easee", "action_command", {
                        "device_id": car["easee_device"], "action_command": "pause"}, blocking=False)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("SmartHus kunne ikke sætte Easee på pause: %s", err)
                # Easee's pause sætter laderens dynamiske strøm til 0 A – den skal sendes igen ved næste start
                self._ev_current_sent.pop(cid, None)

    def _track_fault(self) -> None:
        """Ladefejl: bilen lader med fuld strøm fra nettet, men trækker over 5 minutter mindst 20 % mindre end
        ladeeffekten (fx en fase, der er faldet ud). Advarslen bliver stående, til bilen tages ud af laderen."""
        faults = self.__dict__.setdefault("ev_fault", {})
        since = self.__dict__.setdefault("_ev_fault_since", {})
        now = time.monotonic()
        for car in self.cars:
            cid = car["id"]
            if not self.connected.get(cid, True):
                faults.pop(cid, None); since.pop(cid, None)
                continue
            if not car.get("power"):
                continue
            kw = self._power_kw(car.get("power"))
            ep = self.plan.evs.get(cid) if getattr(self, "plan", None) is not None else None
            st = self.ev_solar.get(cid)
            expected = float(car_settings(self.settings, cid)["charge_power"])
            soc = self.car_soc(car)
            low = (ep is not None and ep.on and not (st is not None and st.active) and kw is not None
                   and kw > 1.0 and expected > 1.5 and kw < EV_FAULT_RATIO * expected
                   and (soc is None or soc < EV_FAULT_MAX_SOC))
            if not low:
                since.pop(cid, None)
                continue
            t0 = since.setdefault(cid, now)
            if now - t0 >= EV_FAULT_SEC and cid not in faults:
                faults[cid] = {"kw": round(kw, 1), "expected_kw": round(expected, 1),
                               "since": dt_util.now().isoformat(timespec="minutes")}
                _LOGGER.warning("SmartHus: %s lader kun med %.1f kW (forventet ca. %.1f kW) – begrænset ladestrøm",
                                car.get("name") or cid, kw, expected)

    def _soc_pending(self, car: dict) -> bool:
        """Lige efter en genstart er bilens batteriniveau ofte ikke hentet endnu. Planlæg ikke ud fra
        ladetimer i stedet (det kan starte laderen med det samme) – vent op til 5 min på bilens måler."""
        born = self.__dict__.setdefault("_born", time.monotonic())
        if time.monotonic() - born > EV_SOC_WAIT_SEC or getattr(self, "ev_force", {}).get(car["id"], False):
            return False
        return self.has_soc_sensor(car) and not self.soc_sensor_ok(car)

    def _ev_on_slot(self, cid: str):
        """Kvarteret, hvor bilen allerede lader efter planen (fra sidste beregning) – eller None."""
        prev = self.plan.evs.get(cid) if getattr(self, "plan", None) is not None else None
        cur = getattr(self.plan, "current", None) if prev is not None else None
        if prev is None or cur is None or not prev.on or self.ev_force.get(cid, False):
            return None
        return cur.start

    def _track_soc(self) -> None:
        """Tæl den energi, der er ladet, siden bilen sidst meldte et nyt batteriniveau."""
        now = dt_util.utcnow()
        for car in self.cars:
            cid = car["id"]
            if not self.soc_sensor_ok(car):
                self._track_manual(car, now)      # ingen sensor – eller den svarer ikke: brug det indtastede tal
                continue
            if self.has_soc_sensor(car):
                self.manual_soc.pop(cid, None)    # sensoren virker igen: det indtastede tal er forældet
            rep = self._float_state(car.get("soc"))
            if rep is None:
                continue
            st = self._soc_est.get(cid)
            if st is None or abs(rep - st["base"]) >= 0.5:
                self._soc_est[cid] = {"base": rep, "kwh": 0.0}      # nyt tal fra bilen
                continue
            kw = self._power_kw(car.get("power")) if car.get("power") else None
            if kw is not None and kw > 0.3 and self.connected.get(cid, True):
                st["kwh"] += kw * TICK_SECONDS / 3600

    def car_soc(self, car: dict) -> float | None:
        """Bilens batteriniveau – skønnet ud fra den ladede energi, hvis bilen ikke har meldt et
        nyt tal, mens den lader (så den ikke lader forbi lademålet på et forældet tal)."""
        if not self.soc_sensor_ok(car):
            return self.manual_soc_value(car["id"])
        rep = self._float_state(car.get("soc"))
        st = self._soc_est.get(car["id"])
        if rep is None or not st or not st["kwh"]:
            return rep
        cap = max(float(car_settings(self.settings, car["id"])["battery_capacity"]), 1.0)
        est = st["base"] + st["kwh"] * CHARGE_EFFICIENCY / cap * 100
        return round(min(max(rep, est), 100.0), 1)

    def car_soc_reported_at(self, car: dict) -> str | None:
        """Hvornår bilen sidst sendte et nyt batteriniveau."""
        st = self.hass.states.get(car.get("soc")) if car.get("soc") else None
        return st.last_changed.isoformat() if st is not None else None

    @staticmethod
    def _charger_is_select(car: dict) -> bool:
        return (car.get("charger") or "").split(".")[0] in ("select", "input_select")

    def _charger_on(self, car: dict) -> bool:
        """Er laderen tændt? En lader kan styres med en kontakt eller en rullemenu (fx go-e, Wattpilot, Garo)."""
        entity_id = car.get("charger")
        if not entity_id:
            return False
        if self._charger_is_select(car):
            st = self.hass.states.get(entity_id)
            return st is not None and st.state == car.get("charger_on")
        return self._is_on(entity_id)

    async def _set_charger(self, car: dict, on: bool, force: bool = False) -> None:
        if self._charger_is_select(car):
            option = car.get("charger_on") if on else car.get("charger_off")
            if option:
                await self._ensure_option(car["charger"], option, force)
            return
        await self._ensure_switch(car["charger"], on, force)

    def _is_on(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == STATE_ON

    async def _ensure_switch(self, entity_id: str, on: bool, force: bool = False) -> None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == STATE_UNAVAILABLE:
            return
        if (st.state == STATE_ON) == on and not (force and self._recently_sent(entity_id)):
            return       # (lige efter en kommando kan tilstanden være forældet – så sendes den alligevel)
        if not force and not self._rate_ok(entity_id):
            return
        domain = entity_id.split(".")[0]
        await self._call(domain if domain in ("switch", "input_boolean", "light", "fan") else "homeassistant",
                         "turn_on" if on else "turn_off", {"entity_id": entity_id})

    async def _ensure_option(self, entity_id: str, option: str, force: bool = False) -> None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == STATE_UNAVAILABLE or not option:
            return
        if st.state == option:
            return
        options = st.attributes.get("options") or []
        if options and option not in options:
            _LOGGER.warning("Tilstanden '%s' findes ikke på %s (mulige: %s)", option, entity_id, options)
            return
        if not force and not self._rate_ok(entity_id):
            return
        domain = "input_select" if entity_id.startswith("input_select.") else "select"
        await self._call(domain, "select_option", {"entity_id": entity_id, "option": option})

    async def _ensure_temperature(self, entity_id: str, temp: float, force: bool = False) -> None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == STATE_UNAVAILABLE:
            return
        current = st.attributes.get("temperature")
        try:
            if current is not None and abs(float(current) - temp) < 0.05:
                return
        except (TypeError, ValueError):
            pass
        if not force and not self._rate_ok(entity_id):
            return
        domain = entity_id.split(".")[0]
        await self._call(domain, "set_temperature", {"entity_id": entity_id, "temperature": round(temp, 1)})

    async def _run_script(self, key: str, group: str, state: str, force: bool, variables: dict) -> bool:
        """Kør scriptet for en tilstand – kun når tilstanden skifter (eller ved force).

        Returnerer True, hvis der er et script for tilstanden (så skal intet andet styres).
        """
        script = self.config.get(key)
        if not script:
            return False
        if not force and self._script_state.get(group) == state:
            return True
        if not force and not self._rate_ok(f"{group}:{script}"):
            return True
        self._script_state[group] = state
        self._last_cmd[f"{group}:{script}"] = time.monotonic()
        try:
            await self.hass.services.async_call(
                "script", "turn_on", {"entity_id": script, "variables": variables}, blocking=False
            )
            _LOGGER.info("SmartHus kørte %s (%s: %s)", script, group, state)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus kunne ikke køre %s: %s", script, err)
        return True

    async def _ensure_number(self, entity_id: str, value: float, force: bool = False) -> None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == STATE_UNAVAILABLE:
            return
        try:
            if abs(float(st.state) - value) < 1e-6:
                return
        except ValueError:
            pass
        if not force and not self._rate_ok(entity_id):
            return
        domain = "input_number" if entity_id.startswith("input_number.") else "number"
        await self._call(domain, "set_value", {"entity_id": entity_id, "value": value})

    def _script_vars(self, state: str) -> dict:
        cur = self.plan.current
        return {
            "smarthus_state": state,
            "price": cur.price if cur else None,
            "sale_price": cur.sale if cur else None,
            "min_soc": self.settings.get("battery_min_soc"),
            "max_soc": self.settings.get("battery_max_soc"),
        }

    async def _apply_battery(self, action: str, force: bool) -> None:
        key = {ACTION_CHARGE: CONF_BATTERY_SCRIPT_CHARGE, ACTION_DISCHARGE: CONF_BATTERY_SCRIPT_DISCHARGE,
               ACTION_SELL_SOLAR: CONF_BATTERY_SCRIPT_SELL_SOLAR,
               ACTION_HOLD: CONF_BATTERY_SCRIPT_HOLD}.get(action, CONF_BATTERY_SCRIPT_NORMAL)
        if action == ACTION_HOLD and not self.config.get(key) and any(self.config.get(k) for k in BATTERY_SCRIPTS):
            key = CONF_BATTERY_SCRIPT_NORMAL       # scripts uden "gem": normal drift
        if await self._run_script(key, "battery", action, force, self._script_vars(action)):
            return
        if self.config.get(CONF_BATTERY_ACTIONS):
            await self._apply_preset(action, force)
            return
        if self.config.get(CONF_HUAWEI_DEVICE):
            await self._apply_huawei(action, force)
            return
        if self.config.get(CONF_GROWATT_DEVICE):
            await self._apply_growatt(action, force)
            return
        if self.config.get(CONF_BATTERY_MODE_ENTITY):
            await self._ensure_option(self.config[CONF_BATTERY_MODE_ENTITY], self._battery_option(action), force)

    async def _apply_export(self, stop: bool, force: bool) -> None:
        state = "stop" if stop else "normal"
        key = CONF_EXPORT_SCRIPT_STOP if stop else CONF_EXPORT_SCRIPT_START
        if await self._run_script(key, "export", state, force, self._script_vars(state)):
            return
        entity_id = self.config.get(CONF_SOLAR_CURTAIL)
        if not entity_id:
            return
        domain = entity_id.split(".")[0]
        if domain in ("select", "input_select"):
            option = self.config.get(CONF_EXPORT_OPT_STOP if stop else CONF_EXPORT_OPT_NORMAL)
            if option:
                await self._ensure_option(entity_id, option, force)
        elif domain in ("number", "input_number"):
            value = self.config.get(CONF_EXPORT_VALUE_STOP if stop else CONF_EXPORT_VALUE_NORMAL)
            if value is not None:
                await self._ensure_number(entity_id, float(value), force)
        else:
            await self._ensure_switch(entity_id, stop, force)

    async def _service_once(self, group: str, state: str, force: bool, domain: str, service: str,
                            data: dict) -> None:
        """Kald en handling, men kun når tilstanden skifter (eller ved force)."""
        if not force and self._script_state.get(group) == state:
            return
        if not force and not self._rate_ok(f"{group}:{domain}.{service}"):
            return
        self._script_state[group] = state
        self._last_cmd[f"{group}:{domain}.{service}"] = time.monotonic()
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
            _LOGGER.info("SmartHus: %s.%s (%s: %s)", domain, service, group, state)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("SmartHus kunne ikke kalde %s.%s: %s", domain, service, err)

    async def _apply_preset(self, action: str, force: bool) -> None:
        """Færdig opsætning for et kendt anlæg: kør trinnene for tilstanden – kun når den skifter."""
        actions: dict = self.config[CONF_BATTERY_ACTIONS]
        state = action if action in actions else ACTION_NORMAL
        if not force and self._script_state.get("battery") == state:
            return
        if not force and not self._rate_ok("battery:preset"):
            return
        self._script_state["battery"] = state
        self._last_cmd["battery:preset"] = time.monotonic()
        _LOGGER.info("SmartHus: batteri → %s (%d trin)", state, len(actions.get(state, [])))
        for step in actions.get(state, []):
            try:
                await self._run_step(step)
            except Exception as err:  # noqa: BLE001 – ét trin må ikke stoppe resten
                _LOGGER.warning("SmartHus kunne ikke udføre %s på %s: %s", step.get("type"), step.get("entity_id"), err)

    async def _run_step(self, step: dict) -> None:
        kind, entity_id, value = step["type"], step["entity_id"], step.get("value")
        if value == "max_soc":
            value = self.settings.get("battery_max_soc", 95)
        elif value == "min_soc":
            value = self.settings.get("battery_min_soc", 15)
        if kind == "select":
            await self._ensure_option(entity_id, str(value), True)
        elif kind == "number":
            await self._ensure_number(entity_id, float(value), True)
        elif kind == "switch":
            await self._ensure_switch(entity_id, bool(value), True)
        elif kind == "time":
            await self._call("time", "set_value", {"entity_id": entity_id, "time": value})
        elif kind == "button":
            await self._call("button", "press", {"entity_id": entity_id})

    def _huawei_discharge_entity(self) -> str | None:
        """Huawei: tallet "maks. afladeeffekt" (storage_maximum_discharging_power) – 0 W = batteriet gemmes."""
        cached = self.__dict__.get("_hw_discharge_ent", False)
        if cached is not False:
            return cached
        found = None
        try:
            from homeassistant.helpers import entity_registry as er
            reg = er.async_get(self.hass)
            device = self.config.get(CONF_HUAWEI_DEVICE)
            cands = [e for e in reg.entities.values() if e.platform == "huawei_solar" and e.domain == "number"
                     and (e.unique_id or "").endswith("_" + HUAWEI_DISCHARGE_KEY) and not e.disabled_by]
            cands.sort(key=lambda e: e.device_id != device)          # helst på batteriets egen enhed
            found = cands[0].entity_id if cands else None
        except Exception:  # noqa: BLE001
            found = None
        self.__dict__["_hw_discharge_ent"] = found
        return found

    async def _apply_huawei(self, action: str, force: bool) -> None:
        """Huawei (huawei_solar): tvungen opladning/afladning via integrationens egne handlinger. "Gem batteriet"
        sætter maks. afladeeffekt til 0 W (solen må stadig lade); de andre tilstande sætter den tilbage til maks."""
        device = self.config[CONF_HUAWEI_DEVICE]
        ent = self._huawei_discharge_entity()
        if action == ACTION_HOLD and ent is None:
            action = ACTION_NORMAL
        if ent is not None and (force or self._script_state.get("battery") != action):
            st = self.hass.states.get(ent)
            limit = 0 if action == ACTION_HOLD else (st.attributes.get("max") if st is not None else None)
            if limit is not None and (force or st is None or self._float_state(ent) != float(limit)):
                try:
                    await self.hass.services.async_call("number", "set_value",
                                                        {"entity_id": ent, "value": limit}, blocking=False)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("SmartHus kunne ikke sætte %s: %s", ent, err)
        if action == ACTION_HOLD:
            await self._service_once("battery", action, force, "huawei_solar", "stop_forcible_charge",
                                     {"device_id": device})
            return
        power = int(float(self.config.get(CONF_HUAWEI_POWER) or DEFAULT_HUAWEI_POWER))
        if action == ACTION_CHARGE:
            await self._service_once("battery", action, force, "huawei_solar", "forcible_charge_soc", {
                "device_id": device, "target_soc": int(self.settings.get("battery_max_soc", 95)), "power": power})
        elif action == ACTION_DISCHARGE:
            await self._service_once("battery", action, force, "huawei_solar", "forcible_discharge_soc", {
                "device_id": device, "target_soc": int(self.settings.get("battery_min_soc", 15)), "power": power})
        else:
            await self._service_once("battery", action, force, "huawei_solar", "stop_forcible_charge",
                                     {"device_id": device})

    def _track_hold_sun(self) -> bool:
        """Skal "gem batteriet" vige for solen lige nu?

        "Gem" på Growatt SPH er Battery First med 1 % ladeeffekt – det stopper også solens opladning, så
        overskuddet sælges, selv om batteriet har plads. Giver solen overskud (eksport, eller batteriet lader i
        normal drift) i 2 minutter, køres normal drift, så overskuddet lader batteriet. Begynder batteriet at
        dække huset (solen er for svag) i 3 minutter, gemmes det igen. Ventetiderne holder skyen fri for skift.
        """
        exp, bat = self.export_kw, self.battery_kw
        if exp is None and bat is None:
            return self.hold_sun
        surplus = (exp or 0.0) - (bat or 0.0)       # det solen giver ud over huset (bat positiv = afladning)
        soc = self._float_state(self.config.get(CONF_BATTERY_SOC))
        full = soc is not None and soc >= float(self.settings.get(S_BATTERY_MAX_SOC, 100)) - 0.5
        now = time.monotonic()
        if self.hold_sun:
            change, wait = surplus < -HOLD_SUN_OFF_KW, HOLD_SUN_OFF_SEC
        else:
            change, wait = surplus >= HOLD_SUN_ON_KW and not full, HOLD_SUN_ON_SEC
        if not change:
            self._hold_sun_since = None
        elif self._hold_sun_since is None:
            self._hold_sun_since = now
        elif now - self._hold_sun_since >= wait:
            self.hold_sun, self._hold_sun_since = not self.hold_sun, None
            _LOGGER.info("SmartHus: gem batteriet – %s", "solen lader batteriet" if self.hold_sun else "batteriet gemmes igen")
        return self.hold_sun

    async def _apply_growatt(self, action: str, force: bool) -> None:
        """Growatt via skyen: skriv lade-/afladetidsrummet for den aktuelle blok – kun når blokken skifter.

        Tilstanden huskes først, når Growatt har taget imod alle kald; fejler et kald, prøves der igen
        næste gang (højst hvert 90. sekund), så en kortvarig fejl i skyen ikke efterlader en halv opsætning.
        """
        kind = self.config.get(CONF_GROWATT_TYPE) or gw.TYPE_SPH
        if action == ACTION_HOLD and kind == gw.TYPE_SPH and self._track_hold_sun():
            action = ACTION_NORMAL        # solen giver overskud: normal drift, så overskuddet lader batteriet
        elif action != ACTION_HOLD:
            self.hold_sun, self._hold_sun_since = False, None
        if action not in (ACTION_CHARGE, ACTION_DISCHARGE, ACTION_HOLD) or (action == ACTION_HOLD and kind != gw.TYPE_SPH):
            action = ACTION_NORMAL        # "sælg sol" findes ikke via skyen, og MIN kan ikke stå stille
        window = None
        cur = self.plan.current if getattr(self, "plan", None) else None
        if action != ACTION_NORMAL and cur is not None:
            slots = {ACTION_CHARGE: self.plan.battery_charge, ACTION_DISCHARGE: self.plan.battery_discharge,
                     ACTION_HOLD: self.plan.battery_hold}[action]
            window = gw.block_window(slots, cur, self.tz)
        state = f"{action}@{window[0]}-{window[1]}" if window else action
        if not force and self._script_state.get("battery") == state:
            return
        if not force and not self._rate_ok("battery:growatt"):
            return
        self._last_cmd["battery:growatt"] = time.monotonic()
        cmds = gw.commands(kind, self.config[CONF_GROWATT_DEVICE],
                           action, window, int(self.settings.get("battery_max_soc", 95)),
                           int(self.settings.get("battery_min_soc", 15)),
                           int(float(self.config.get(CONF_GROWATT_POWER) or DEFAULT_GROWATT_POWER)))
        for service, data in cmds:
            try:
                await self.hass.services.async_call(gw.DOMAIN, service, data, blocking=True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SmartHus: Growatt %s fejlede (%s) – prøver igen om lidt", service, err)
                self._script_state.pop("battery", None)
                return
        self._script_state["battery"] = state
        _LOGGER.info("SmartHus: Growatt batteri → %s", state)

    def _battery_option(self, action: str) -> str:
        key = {
            ACTION_CHARGE: CONF_BATTERY_OPT_CHARGE,
            ACTION_DISCHARGE: CONF_BATTERY_OPT_DISCHARGE,
            ACTION_SELL_SOLAR: CONF_BATTERY_OPT_SELL_SOLAR,
            ACTION_HOLD: CONF_BATTERY_OPT_HOLD,
        }.get(action, CONF_BATTERY_OPT_NORMAL)
        if key in (CONF_BATTERY_OPT_SELL_SOLAR, CONF_BATTERY_OPT_HOLD):
            return self.config.get(key) or self.config.get(CONF_BATTERY_OPT_NORMAL) \
                or DEFAULT_BATTERY_OPTIONS[CONF_BATTERY_OPT_NORMAL]
        return self.config.get(key) or DEFAULT_BATTERY_OPTIONS[key]

    async def async_apply(self, force: bool = False) -> None:
        """Bring enhederne i den tilstand planen ønsker. En fejl i ét modul stopper ikke de andre."""
        p = self.plan
        if p.current is None:
            return
        for name, step in (("batteri", self._apply_battery_step), ("eksport", self._apply_export_step),
                           ("elbil", self._apply_ev_step), ("varmt vand", self._apply_hotwater_step),
                           ("varme", self._apply_heating_step)):
            try:
                await step(p, force)
            except Exception:  # noqa: BLE001 – ét modul må ikke stoppe resten af styringen
                _LOGGER.exception("SmartHus: fejl i styringen af %s", name)

    def ev_from_grid(self) -> list[str]:
        """Biler, der lader fra nettet lige nu (ikke solcelleladning): laderen trækker mindst 1 kW – eller,
        uden effektmåler, planen har tændt den."""
        out = []
        for car in self.cars:
            cid = car["id"]
            if not self.flags.get(f"{cid}_keep_battery", True):
                continue                      # kunden har slået "gem husbatteriet under opladning" fra
            st = self.ev_solar.get(cid)
            if st is not None and st.active:
                continue
            ep = self.plan.evs.get(cid)
            planned_on = ep is not None and ep.on and ep.connected and self.active(cid)
            kw = self._power_kw(car.get("power"))
            # Med effektmåler tæller det, bilen faktisk trækker (fuld bil = intet at gemme); ellers planen
            if (kw >= EV_PROTECT_KW) if kw is not None else planned_on:
                out.append(cid)
        return out

    async def _apply_battery_step(self, p, force: bool) -> None:
        if self.active(MODULE_BATTERY) and self.has_battery_control:
            # Lader elbilen fra nettet, må husbatteriet ikke tømmes ned i bilen: gem det i stedet
            self.ev_protect = False
            if p.battery_action == ACTION_NORMAL and self.has_hold and self.ev_from_grid():
                p.battery_action = ACTION_HOLD
                self.ev_protect = True
            await self._apply_battery(p.battery_action, force)

    async def _apply_export_step(self, p, force: bool) -> None:
        if self.active(MODULE_SOLAR) and self.has_export_control:
            await self._apply_export(p.solar_curtail, force)

    async def _apply_ev_step(self, p, force: bool) -> None:
        solar_car = next((cid for cid, st in self.ev_solar.items() if st.active), None)
        for car in self.cars:
            ep = p.evs.get(car["id"])
            if ep and not ep.connected and car["charger"] in self.shared_chargers:
                self.ev_solar.get(car["id"], EvSolarState()).reset()
                continue        # en anden bil sidder i laderen – den styrer laderen
            if ep and self.active(car["id"]):
                on, current = self._ev_target(car, ep, solar_car, step=not force)
                if self.ev_solar.get(car["id"]) and self.ev_solar[car["id"]].active:
                    solar_car = car["id"]
                if on and self.ev_on_hold(car["id"]) and not self._charger_on(car):
                    continue        # brugeren er ved at ændre klar-tidspunktet – start ikke endnu
                await self._set_charger(car, on, force)
                if not on and car.get("easee_device"):
                    # Slukket: send ladestrømmen igen ved næste start (en pause – fra SmartHus, appen eller
                    # Easee selv – kan have sat den dynamiske strøm til 0 A, så laderen ikke lader)
                    self._ev_current_sent.pop(car["id"], None)
                if on and current is not None:
                    await self._apply_current(car, current, force)
                await self._ev_verify_off(car, on)
            elif ep:
                await self._apply_ev_manual(car, ep, force)

    async def _apply_hotwater_step(self, p, force: bool) -> None:
        if self.active(MODULE_HOTWATER) and self.has(CONF_HOTWATER_ENTITY):
            await self._apply_hotwater(p.hotwater_on, force)

    # --- Varme ------------------------------------------------------------
    @property
    def heating_air_to_air(self) -> bool:
        """Lille luft-til-luft varmepumpe (fx vinterbeskyttelse i sommerhus)."""
        return self.config.get(CONF_HEATING_TYPE) == HEATING_TYPE_AIR

    @property
    def has_heating_scripts(self) -> bool:
        return self.heating_air_to_air and any(
            self.config.get(k) for k in (CONF_HEATING_SCRIPT_NORMAL, CONF_HEATING_SCRIPT_BOOST, CONF_HEATING_SCRIPT_REDUCE))

    @property
    def has_heating_control(self) -> bool:
        return bool(self.config.get(CONF_HEATING_CLIMATE)) or self.has_heating_scripts

    @property
    def indoor_temp(self) -> float | None:
        if not self.heating_air_to_air:
            return None
        return self._float_state(self.config.get(CONF_HEATING_INDOOR_TEMP))

    @property
    def heating_off_in_reduce(self) -> bool:
        return (self.heating_air_to_air and bool(self.config.get(CONF_HEATING_CLIMATE))
                and bool(self.flags.get(F_HEATING_OFF_REDUCE, False)))

    def _update_frost_guard(self) -> bool:
        """Frostsikring: under mindste indetemperatur springes sparetimer over (1 °C hysterese)."""
        temp = self.indoor_temp
        if temp is None:
            self.heating_too_cold = False
        else:
            limit = float(self.settings.get(S_HEATING_MIN_INDOOR, 8.0))
            if temp < limit:
                self.heating_too_cold = True
            elif temp >= limit + 1.0:
                self.heating_too_cold = False
        return self.heating_too_cold

    async def _ensure_hvac(self, entity_id: str, mode: str, force: bool = False) -> None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == STATE_UNAVAILABLE or st.state == mode:
            return
        if not force and not self._rate_ok(entity_id):
            return
        await self._call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode})

    async def _set_heating(self, mode: str, force: bool) -> None:
        """Sæt varmen i tilstand normal / boost / reduce – med termostat eller scripts."""
        base = self.settings[S_HEATING_BASE_TEMP]
        temp = base + self.settings[S_HEATING_BOOST_DEG] if mode == "boost" else \
            base - self.settings[S_HEATING_REDUCE_DEG] if mode == "reduce" else base
        if self.heating_air_to_air:
            key = {"normal": CONF_HEATING_SCRIPT_NORMAL, "boost": CONF_HEATING_SCRIPT_BOOST,
                   "reduce": CONF_HEATING_SCRIPT_REDUCE}[mode]
            # Scripts (fx IR/RF-signaler) køres kun, når tilstanden skifter
            if await self._run_script(key, "heating", mode, force, {"mode": mode, "temperature": round(temp, 1)}):
                return
        climate = self.config.get(CONF_HEATING_CLIMATE)
        if not climate:
            return
        if mode == "reduce" and self.heating_off_in_reduce:
            await self._ensure_hvac(climate, "off", force)
            self._heating_off_by_us = True
            return
        if self._heating_off_by_us:
            # Tænd igen efter sparetimerne (kun hvis det var SmartHus, der slukkede)
            await self._ensure_hvac(climate, "heat", True)
            self._heating_off_by_us = False
        await self._ensure_temperature(climate, temp, force)

    async def _apply_heating_step(self, p, force: bool) -> None:
        if not (self.active(MODULE_HEATING) and self.has_heating_control):
            return
        if not self._update_heating_outdoor():
            if not self._heating_paused_applied or force:
                # sæt grundtemperaturen én gang, og lad så termostaten være
                await self._set_heating("normal", True)
                self._heating_paused_applied = True
            return
        self._heating_paused_applied = False
        mode = "boost" if p.heating_mode == HEATING_BOOST else "reduce" if p.heating_mode == HEATING_REDUCE else "normal"
        if mode == "reduce" and self._update_frost_guard():
            mode = "normal"      # for koldt inde – spar ikke
        await self._set_heating(mode, force)

    def _plug(self, car: dict) -> bool | None:
        entity_id = car.get("plug")
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        return plug_state(st.state if st else None, car.get("plug_off") or ())

    def _update_connected(self) -> None:
        """Find ud af, hvilke biler der sidder i laderen."""
        for car in self.cars:
            if car["charger"] in self.shared_chargers:
                continue
            plug = self._plug(car)
            self.connected[car["id"]] = plug is not False
            self.connected_source[car["id"]] = "sensor" if plug is not None else "assumed"
        for charger, ids in self.shared_chargers.items():
            plugs = {cid: self._plug(self.car(cid)) for cid in ids}
            active = pick_connected(ids, plugs, self.connected_choice.get(charger))
            for cid in ids:
                self.connected[cid] = cid == active
                self.connected_source[cid] = "sensor" if plugs.get(cid) is not None else "manual"

    def has_current_control(self, car: dict) -> bool:
        return bool(car.get("current_entity") or car.get("current_script") or car.get("easee_device"))

    def _ev_target(self, car: dict, ep, solar_car: str | None, step: bool = True) -> tuple[bool, int | None]:
        """Skal laderen være tændt, og med hvilken strøm (A)? None = rør ikke strømmen."""
        cid = car["id"]
        if not ep.connected:
            self.ev_solar.setdefault(cid, EvSolarState()).reset()
            return False, None
        cs = car_settings(self.settings, cid)
        min_a, max_a = int(cs["solar_min_a"]), int(cs["solar_max_a"])
        full = self.full_current(car)
        st = self.ev_solar.setdefault(cid, EvSolarState())
        control = self.has_current_control(car)
        solar_on = control and self.flags.get(f"{cid}_solar", False) and self.export_kw is not None
        if not solar_on or ep.target_reached:
            st.reset()
            return ep.on, (full if control and ep.on and not ep.target_reached else None)
        if ep.on or self.plan.solar_curtail:
            # Billig time, "Lad nu" eller eksporten er stoppet: lad med fuld strøm (laderens maks.)
            st.reset()
            return True, full
        if solar_car not in (None, cid):
            st.reset()                   # en anden bil lader allerede med sol
            return False, None
        if step:  # kun én reguleringsrunde pr. 30 sekunder
            regulate_ev(st, self.export_kw, self.solar_kw, min_a, max_a, cs["solar_start_kw"])
        return (True, st.current) if st.active else (False, None)

    def full_current(self, car: dict) -> int:
        """Fuld ladestrøm, når bilen lader fra nettet: laderens egen maks. – ikke solcelleladningens grænse.
        Easee begrænser selv til laderens maks. (32 A er det højeste, den tager imod)."""
        if car.get("easee_device"):
            return EASEE_FULL_A
        ent = car.get("current_entity")
        st = self.hass.states.get(ent) if ent else None
        mx = st.attributes.get("max") if st is not None else None
        try:
            if mx is not None and float(mx) >= 6:
                return int(float(mx))
        except (TypeError, ValueError):
            pass
        return int(car_settings(self.settings, car["id"])["solar_max_a"])   # script/ukendt: højeste valgte strøm

    async def _apply_current(self, car: dict, current: int, force: bool) -> None:
        cid = car["id"]
        if not force and self._ev_current_sent.get(cid) == current:
            return
        self._ev_current_sent[cid] = current
        if car.get("current_script"):
            try:
                await self.hass.services.async_call(
                    "script", "turn_on",
                    {"entity_id": car["current_script"], "variables": {"current": current, "car": car.get("name")}},
                    blocking=False)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SmartHus kunne ikke køre %s: %s", car["current_script"], err)
        elif car.get("easee_device"):
            try:
                await self.hass.services.async_call("easee", "set_charger_dynamic_limit", {
                    "device_id": car["easee_device"], "current": current, "time_to_live": 0}, blocking=False)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SmartHus kunne ikke sætte Easee-ladestrøm: %s", err)
        elif car.get("current_entity"):
            await self._ensure_number(car["current_entity"], float(current), True)

    async def _apply_hotwater(self, on: bool, force: bool) -> None:
        entity_id = self.config[CONF_HOTWATER_ENTITY]
        if entity_id.split(".")[0] in ("water_heater", "climate"):
            temp = self.settings[S_HOTWATER_BOOST_TEMP if (on or self.hotwater_surplus) else S_HOTWATER_NORMAL_TEMP]
            await self._ensure_temperature(entity_id, temp, force)
        elif entity_id.split(".")[0] in ("button", "input_button"):
            # Knap (fx varmepumpens "charge"): tryk én gang, når opvarmningen skal starte
            want = self._hotwater_switch_on(on)
            if want and not self._hw_button_on:
                domain = entity_id.split(".")[0]
                await self._call(domain, "press", {"entity_id": entity_id})
            self._hw_button_on = want
        else:
            await self._ensure_switch(entity_id, self._hotwater_switch_on(on), force)

    @property
    def export_kw(self) -> float | None:
        """Eksport til nettet lige nu i kW (negativ = køb fra nettet)."""
        entity_id = self.config.get(CONF_GRID_POWER)
        if not entity_id:
            return self._pair_kw(CONF_GRID_EXPORT_POWER, CONF_GRID_IMPORT_POWER)
        val = self._float_state(entity_id)
        if val is None:
            return None
        unit = (self.hass.states.get(entity_id).attributes.get("unit_of_measurement") or "W").lower()
        kw = val / 1000 if unit == "w" else val * 1000 if unit == "mw" else val
        return -kw if self.config.get(CONF_GRID_POWER_SIGN) == SIGN_IMPORT_POSITIVE else kw

    @property
    def solar_kw(self) -> float | None:
        """Solcelleproduktion lige nu i kW (hvis der er valgt en sensor)."""
        entity_id = self.config.get(CONF_SOLAR_POWER)
        val = self._float_state(entity_id)
        if val is None:
            return None
        unit = (self.hass.states.get(entity_id).attributes.get("unit_of_measurement") or "W").lower()
        return val / 1000 if unit == "w" else val * 1000 if unit == "mw" else val

    def _pair_kw(self, plus_key: str, minus_key: str) -> float | None:
        """To sensorer, der hver viser én retning (fx salg og køb): plus − minus i kW.
        Kræver begge; tal under 0 i den enkelte sensor tæller som 0."""
        plus = self._power_kw(self.config.get(plus_key))
        minus = self._power_kw(self.config.get(minus_key))
        if plus is None or minus is None:
            return None
        return max(plus, 0.0) - max(minus, 0.0)

    def _power_kw(self, entity_id: str | None) -> float | None:
        val = self._float_state(entity_id)
        if val is None:
            return None
        return to_kw(val, self.hass.states.get(entity_id).attributes.get("unit_of_measurement"))

    @property
    def battery_kw(self) -> float | None:
        """Batteriets effekt lige nu i kW (positiv = afladning)."""
        if not self.config.get(CONF_BATTERY_POWER):
            return self._pair_kw(CONF_BATTERY_DISCHARGE_POWER, CONF_BATTERY_CHARGE_POWER)
        kw = self._power_kw(self.config.get(CONF_BATTERY_POWER))
        if kw is None:
            return None
        return -kw if self.config.get(CONF_BATTERY_POWER_SIGN) == SIGN_CHARGE_POSITIVE else kw

    @property
    def flow(self) -> dict:
        """Energiflowet lige nu – bruges af kortets 3D-visning."""
        ev = {car["id"]: self._power_kw(car.get("power")) for car in self.cars}
        f = energy_flow(self.solar_kw, self.export_kw, self.battery_kw, ev)
        f["battery_soc"] = self._float_state(self.config.get(CONF_BATTERY_SOC))
        f["has_battery"] = self.has_battery_control or bool(self.config.get(CONF_BATTERY_POWER)
                                                             or self.config.get(CONF_BATTERY_CHARGE_POWER))
        f["has_solar"] = bool(self.config.get(CONF_SOLAR_POWER) or self.config.get(CONF_SOLAR_ENERGY))
        f["cars"] = [{"id": c["id"], "name": c.get("name") or "Elbil", "kw": f["ev_kw"].get(c["id"]),
                      "connected": self.connected.get(c["id"], True),
                      "soc": self._float_state(c.get("soc"))} for c in self.cars]
        f["hotwater_on"] = bool(self.config.get(CONF_HOTWATER_ENTITY)) and bool(
            self.plan.hotwater_on or self.hotwater_surplus) and not self.hotwater_full
        f["has_hotwater"] = bool(self.config.get(CONF_HOTWATER_ENTITY))
        f["has_heatpump"] = bool(self.config.get(CONF_HEATING_CLIMATE))
        f["weather"] = self.weather_info
        return f

    def _today(self) -> str:
        return dt_util.now().date().isoformat()

    @property
    def hotwater_heated_today(self) -> bool:
        return self._hw_done_date == self._today()

    def _next_hotwater_price(self) -> float | None:
        """Prisen i den næste planlagte billige opvarmning (det overskuddet skal slå)."""
        now = dt_util.utcnow()
        future = sorted((sl for sl in self.plan.hotwater_slots if sl.start > now), key=lambda sl: sl.start)
        return future[0].price if future else None

    def surplus_worth_it(self) -> bool:
        """Kan det betale sig at bruge overskuddet nu frem for at sælge det og varme op senere?"""
        cur = self.plan.current
        nxt = self._next_hotwater_price()
        if cur is None or nxt is None:
            return True
        return cur.sale < nxt

    def _update_surplus(self) -> None:
        """Varmt vand med solcelleoverskud – højst én opvarmning om dagen."""
        today = self._today()
        # Er dagens billige opvarmning allerede kørt færdig, er vandet varmet i dag
        todays = [sl for sl in self.plan.hotwater_slots if sl.start.astimezone(self.tz).date().isoformat() == today]
        if todays and max(sl.end for sl in todays) <= dt_util.utcnow():
            self._hw_done_date = today
        limit = float(self.settings.get("hotwater_surplus_kw", 0) or 0)
        export = self.export_kw
        if limit <= 0 or export is None or self._hw_done_date == today:
            self.hotwater_surplus, self._surplus_count = False, 0
            return
        if self.hotwater_surplus:
            # Mens varmelegemet kører, falder eksporten – stop først ved køb fra nettet
            want_change = export < -SURPLUS_STOP_IMPORT_KW
        else:
            smart = self.flags.get(F_HOTWATER_SURPLUS_SMART, True)
            want_change = export >= limit and (not smart or self.surplus_worth_it())
        self._surplus_count = self._surplus_count + 1 if want_change else 0
        if self._surplus_count >= SURPLUS_CONFIRM_TICKS:
            self.hotwater_surplus = not self.hotwater_surplus
            self._surplus_count = 0
        if self.hotwater_surplus and not self.hotwater_full:
            self._hw_surplus_sec += TICK_SECONDS
        # Færdig: maks. temperatur nået eller kørt de indstillede timer
        hours = float(self.settings.get("hotwater_hours", 1) or 1)
        if self.hotwater_surplus and (self.hotwater_full or self._hw_surplus_sec >= hours * 3600):
            self.hotwater_surplus = False
            self._hw_done_date = today
            self._hw_surplus_sec = 0.0

    # --- Vejr og solprognose ---------------------------------------------
    @property
    def weather_entity(self) -> str | None:
        """Valgt vejr-entitet, ellers Home Assistants egen (Met.no 'Hjem') fra skyen."""
        if self.config.get(CONF_WEATHER):
            return self.config[CONF_WEATHER]
        try:
            ids = [s.entity_id for s in self.hass.states.async_all("weather")]
        except AttributeError:
            return None
        return pick_weather_entity(ids)

    def _weather_state(self):
        entity_id = self.weather_entity
        st = self.hass.states.get(entity_id) if entity_id else None
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return st

    @property
    def outdoor_source(self) -> str | None:
        if self._float_state(self.config.get(CONF_OUTDOOR_TEMP)) is not None:
            return "sensor"
        st = self._weather_state()
        return "weather" if st is not None and st.attributes.get("temperature") is not None else None

    @property
    def outdoor_temp(self) -> float | None:
        """Udetemperatur: egen føler, hvis den er valgt og svarer – ellers vejret fra skyen."""
        temp = self._float_state(self.config.get(CONF_OUTDOOR_TEMP))
        if temp is not None:
            return temp
        st = self._weather_state()
        try:
            return float(st.attributes.get("temperature")) if st is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def weather_info(self) -> dict:
        st = self._weather_state()
        sun = self.hass.states.get("sun.sun")
        a = st.attributes if st is not None else {}
        return {
            "entity": self.weather_entity,
            "condition": st.state if st is not None else None,
            "temperature": self.outdoor_temp,
            "temp_source": self.outdoor_source,
            "cloud_coverage": a.get("cloud_coverage"),
            "humidity": a.get("humidity"),
            "wind_speed": a.get("wind_speed"),
            "wind_speed_unit": a.get("wind_speed_unit"),
            "night": sun.state == "below_horizon" if sun is not None else None,
        }

    async def _async_refresh_forecast(self) -> None:
        """Hent timeprognosen fra Forecast.Solar, Solcast eller Open-Meteo Solar Forecast (samme data som energi-dashboardet)."""
        try:
            from homeassistant.loader import async_get_integration
        except ImportError:
            return
        chosen = self.config.get(CONF_FORECAST_SOURCE) or FORECAST_AUTO
        sources = [s for s in FORECAST_SOURCES if chosen in (FORECAST_AUTO, s[0])]
        for domain, key_is_end in sources:
            try:
                entries = self.hass.config_entries.async_entries(domain)
            except AttributeError:
                return
            if not entries:
                continue
            try:
                integration = await async_get_integration(self.hass, domain)
                platform = await integration.async_get_platform("energy")
                wh: dict[str, float] = {}
                for entry in entries:
                    res = await platform.async_get_solar_forecast(self.hass, entry.entry_id)
                    if res and res.get("wh_hours"):
                        wh = merge_wh(wh, {str(k): v for k, v in res["wh_hours"].items()})
            except Exception as err:  # noqa: BLE001 – prognosen er kun til visning
                _LOGGER.debug("Kunne ikke hente solprognose fra %s: %s", domain, err)
                continue
            if wh:
                self._forecast_wh, self._forecast_key_is_end = wh, key_is_end
                self._forecast_domain = domain
                self.solar_cal.set_source(domain)
                self.solar_cal.record(solar_by_hour(wh, self.tz, key_is_end), dt_util.now().astimezone(self.tz))
                self._solar_cal_store.async_delay_save(lambda: self.solar_cal.data, 300)
                return
        self._forecast_wh = {}

    def _forecast_ids(self) -> dict[str, str]:
        """Prognose-sensorerne – findes igen hvert 15. minut (der kan være mange sensorer)."""
        now = time.monotonic()
        at = getattr(self, "_fc_ids_at", None)
        if at is None or now - at > (900 if self._fc_ids.get("today") else 60):
            try:
                existing = {s.entity_id for s in self.hass.states.async_all("sensor")}
            except AttributeError:
                existing = set()
            self._fc_ids = forecast_sensor_ids(self.config.get(CONF_SOLAR_FORECAST), existing)
            self._fc_ids_at = now
        return self._fc_ids

    @property
    def solar_forecast(self) -> dict:
        """Solprognose for i dag, resten af dagen og i morgen + timekurve for i dag."""
        cal = self.solar_cal
        learned = cal.active and bool(self._forecast_wh)
        out = summarize_forecast(self._forecast_wh, dt_util.utcnow(), self.tz, self._forecast_key_is_end,
                                 scale=cal.factor if learned else None) if self._forecast_wh else {}
        ids = self._forecast_ids()
        # Sensorerne er det, brugeren kender – de vinder over vores egen sum.
        # Når prognosen er justeret efter anlægget, er det den justerede sum, der gælder.
        for key, out_key in (("today", "today_kwh"), ("remaining", "remaining_kwh"), ("tomorrow", "tomorrow_kwh")):
            val = self._energy_entity_kwh(ids.get(key))
            if val is None:
                continue
            if learned:
                out[f"unadjusted_{out_key}"] = round(val, 2)
            else:
                out[out_key] = round(val, 2)
        power = self._power_kw(ids.get("power_now"))
        if power is not None:
            out["power_now_kw"] = round(power * (cal.factor(dt_util.utcnow()) if learned else 1.0), 2)
        out["learned_days"] = cal.days
        out["learned_factor"] = cal.data.get("day_factor")
        out["adjusted"] = learned
        out["source"] = self._forecast_domain
        out["available"] = any(out.get(k) is not None for k in ("today_kwh", "remaining_kwh", "tomorrow_kwh"))
        return out

    def _update_heating_outdoor(self) -> bool:
        """Varmestyringen er kun aktiv, når det er koldere ude end grænsen (med 1 °C hysterese).
        Udetemperaturen kommer fra egen føler eller vejret fra skyen. Er der ingen temperatur,
        beholdes den seneste tilstand."""
        temp = self.outdoor_temp
        if temp is not None:
            self.heating_outdoor_ok = heating_outdoor_ok(
                temp, self.settings[S_HEATING_MAX_OUTDOOR], self.heating_outdoor_ok)
        return self.heating_outdoor_ok

    @property
    def hotwater_temp_entity(self) -> str | None:
        """Hvor vandets temperatur aflæses: den valgte føler, ellers vandvarmeren selv (water_heater/climate)."""
        if self.config.get(CONF_HOTWATER_TEMP):
            return self.config[CONF_HOTWATER_TEMP]
        hw = self.config.get(CONF_HOTWATER_ENTITY) or ""
        return hw if hw.startswith(("water_heater.", "climate.")) else None

    def _temp_value(self, entity_id: str | None, st=None) -> float | None:
        """Temperatur fra en sensor (tilstanden) eller fra en water_heater/climate (current_temperature)."""
        if not entity_id:
            return None
        st = st if st is not None else self.hass.states.get(entity_id)
        if st is None:
            return None
        if entity_id.startswith(("water_heater.", "climate.")):
            val = st.attributes.get("current_temperature")
        else:
            # En sensor uden tal i tilstanden, men med current_temperature (fx nogle varmepumpe-integrationer)
            val = st.state if _num(st.state) is not None else st.attributes.get("current_temperature")
        return _water_temp(val)

    @property
    def hotwater_temp(self) -> float | None:
        return self._temp_value(self.hotwater_temp_entity)

    @property
    def hotwater_min_source(self) -> str | None:
        """Navnet på varmepumpens egen nedre grænse, hvis den er valgt og har en værdi."""
        entity_id = self.config.get(CONF_HOTWATER_MIN_ENTITY)
        if not entity_id or self._setpoint_value(entity_id) is None:
            return None
        st = self.hass.states.get(entity_id)
        return st.name if st else entity_id

    def _setpoint_value(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        if entity_id.startswith(("water_heater.", "climate.")):
            val = st.attributes.get("temperature")
        else:
            val = st.state if _num(st.state) is not None else st.attributes.get("temperature")
        return _water_temp(val)

    @property
    def hotwater_min_temp(self) -> float:
        """Laveste temperatur: varmepumpens eget setpunkt, hvis det er valgt – ellers indstillingen."""
        entity_id = self.config.get(CONF_HOTWATER_MIN_ENTITY)
        if entity_id and entity_id == self.config.get(CONF_HOTWATER_ENTITY):
            # SmartHus styrer selv dette setpunkt (hæver det under opvarmning) – brug den normale temperatur
            return float(self.settings.get(S_HOTWATER_NORMAL_TEMP, 45.0))
        val = self._setpoint_value(entity_id) if entity_id else None
        return val if val is not None else float(self.settings.get(S_HOTWATER_MIN_TEMP, 42.0))

    async def _async_sample_hotwater(self) -> None:
        """Gem vandets temperatur (hvert 2. minut) – første gang hentes de seneste timer fra historikken."""
        entity_id = self.hotwater_temp_entity
        if not entity_id:
            return
        now = dt_util.utcnow()
        if not self._hw_seeded:
            self._hw_seeded = True
            try:
                from functools import partial

                from homeassistant.components.recorder import get_instance, history
                # Også ændringer i egenskaber (fx en water_heaters current_temperature)
                hist = await get_instance(self.hass).async_add_executor_job(partial(
                    history.get_significant_states, self.hass, now - timedelta(hours=4), now, [entity_id],
                    significant_changes_only=False))
                for st in hist.get(entity_id, []):
                    val = self._temp_value(entity_id, st)
                    if val is not None:
                        self._hw_samples.append((st.last_updated, val))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("SmartHus: ingen historik for vandtemperaturen: %s", err)
        temp = self.hotwater_temp
        if temp is not None and (not self._hw_samples or now - self._hw_samples[-1][0] >= timedelta(minutes=2)):
            self._hw_samples.append((now, temp))
        cutoff = now - timedelta(hours=4)
        self._hw_samples = [x for x in self._hw_samples if x[0] >= cutoff]

    def _skip_late_hotwater(self, now, temp: float | None) -> None:
        """Dagens almindelige opvarmning, der endnu ikke er kørt, springes over, når morgendagens er billigere.

        Planen tager én opvarmning pr. kalenderdag – sent på dagen er "dagens billigste" bare den billigste af
        de få timer, der er tilbage (fx kl. 23), selv om nattens eller morgendagens timer er billigere. Med en
        temperaturføler sørger ekstra-opvarmningen (plan_demand) for, at vandet ikke bliver for koldt inden da.
        """
        if temp is None or self.hotwater_heated_today:
            return
        today = now.astimezone(self.tz).date()
        mine = [s for s in self.plan.hotwater_slots if s.start.astimezone(self.tz).date() == today]
        if not mine or any(s.start <= now for s in mine):
            return                                  # ingen tilbage i dag – eller den er i gang
        later = [s for s in self.plan.hotwater_slots if s.start.astimezone(self.tz).date() > today]
        if not later:
            return                                  # morgendagens priser kendes ikke endnu
        avg = lambda xs: sum(x.price for x in xs) / len(xs)  # noqa: E731
        if avg(later) < avg(mine) - 0.01:
            skip = {s.start for s in mine}
            self.plan.hotwater_slots = [s for s in self.plan.hotwater_slots if s.start not in skip]
            cur = self.plan.current
            if cur is not None and cur.start in skip:
                self.plan.hotwater_on = False

    def _update_hotwater_demand(self) -> None:
        """Ekstra opvarmning i den billigste time, før vandet bliver så koldt, at varmepumpen selv starter."""
        self.hw_demand_now = False
        if not (self.active(MODULE_HOTWATER) and self.has(CONF_HOTWATER_ENTITY) and self.hotwater_temp_entity):
            self.hw_demand, self.hw_deadline, self.hw_rate = [], None, None
            return
        now = dt_util.utcnow()
        temp = self.hotwater_temp
        self.hw_rate = cooling_rate(sorted(self._hw_samples), now)
        watch = float(self.settings.get(S_HOTWATER_WATCH_TEMP, 45.0))
        floor = self.hotwater_min_temp
        self._skip_late_hotwater(now, temp)
        regular = [s for s in self.plan.hotwater_slots if s.end > now]
        keep = [s for s in self.hw_demand if s.end > now]
        if temp is not None and temp >= watch:
            keep = []                      # vandet er varmet op igen
        demand, deadline = plan_demand(self.coordinator.data or [], now, temp, self.hw_rate, watch, floor,
                                       float(self.settings.get(S_HOTWATER_HOURS, 1.0)), regular)
        # Behold en valgt blok, så længe den stadig når at varme vandet i tide
        if keep and (deadline is None or keep[0].start < deadline):
            demand = keep
        self.hw_demand, self.hw_deadline = demand, deadline
        if demand:
            have = {s.start for s in self.plan.hotwater_slots}
            self.plan.hotwater_slots = sorted(self.plan.hotwater_slots + [s for s in demand if s.start not in have],
                                              key=lambda s: s.start)
            cur = self.plan.current
            self.hw_demand_now = cur is not None and any(s.start == cur.start for s in demand)
            if self.hw_demand_now:
                self.plan.hotwater_on = True

    def _hotwater_switch_on(self, planned_on: bool) -> bool:
        """Med temperaturføler: sluk ved øvre grænse, start igen når vandet er kølet af."""
        if planned_on and not self.hotwater_surplus and not self.hw_demand_now and self.hotwater_heated_today:
            cur = self.plan.current
            if cur is not None and cur.start.astimezone(self.tz).date().isoformat() == self._today():
                planned_on = False      # vandet er allerede varmet op i dag
        planned_on = planned_on or self.hotwater_surplus
        if not planned_on:
            self.hotwater_full = False
            return False
        temp = self.hotwater_temp
        if temp is None:
            return True
        limit = self.settings[S_HOTWATER_BOOST_TEMP]
        if temp >= limit:
            self.hotwater_full = True
        elif temp < limit - HOTWATER_HYSTERESIS:
            self.hotwater_full = False
        return not self.hotwater_full

    async def async_restore_normal(self, module: str) -> None:
        """Sæt et modul tilbage til normal drift, når styringen slås fra."""
        if module == MODULE_BATTERY and self.has_battery_control:
            await self._apply_battery(ACTION_NORMAL, True)
        elif module == MODULE_SOLAR and self.has_export_control:
            await self._apply_export(False, True)
        elif module in self.car_ids:
            # Normal drift for elbilen = laderen slukket (der lades kun, når man selv starter den)
            car = self.car(module)
            if car["charger"] in self.shared_chargers and any(
                    self.active(c) and self.connected.get(c, True) for c in self.shared_chargers[car["charger"]] if c != module):
                return                     # en anden bil i samme lader styres stadig af SmartHus
            await self._set_charger(car, False, True)
        elif module == MODULE_HOTWATER and self.has(CONF_HOTWATER_ENTITY):
            entity_id = self.config[CONF_HOTWATER_ENTITY]
            if entity_id.split(".")[0] in ("water_heater", "climate"):
                await self._ensure_temperature(entity_id, self.settings[S_HOTWATER_NORMAL_TEMP], True)
            elif entity_id.split(".")[0] in ("button", "input_button"):
                self._hw_button_on = False     # varmepumpen styrer selv igen
            else:
                await self._ensure_switch(entity_id, True, True)
        elif module == MODULE_HEATING and self.has_heating_control:
            await self._set_heating("normal", True)

    async def async_set_enabled(self, module: str, on: bool) -> None:
        was = self.active(module) if module != MODULE_MASTER else self.enabled[MODULE_MASTER]
        self.enabled[module] = on
        if was and not on:
            # Normal drift sendes først efter lidt tid: et fejltryk, der rettes med det samme,
            # må ikke tænde laderen eller vandvarmeren et øjeblik
            mods = [m for m in self.modules if self.enabled.get(m)] if module == MODULE_MASTER else [module]
            self._schedule_restore(mods)
        if on:
            for m in (list(self._restore_pending) if module == MODULE_MASTER else [module]):
                self._cancel_restore(m)
        self.recalculate()
        if on:
            await self.async_apply(force=True)

    def _schedule_restore(self, modules: list[str]) -> None:
        for m in modules:
            self._cancel_restore(m)

            async def _run(_now, m=m) -> None:
                self._restore_pending.pop(m, None)
                if not self.active(m):           # stadig slået fra
                    await self.async_restore_normal(m)

            self._restore_pending[m] = async_call_later(self.hass, RESTORE_DELAY_SEC, _run)

    def _cancel_restore(self, module: str) -> None:
        unsub = self._restore_pending.pop(module, None)
        if callable(unsub):
            unsub()

    async def async_set_setting(self, key: str, value: float) -> None:
        self.settings[key] = value
        for cid in self.car_ids:
            if key in (f"{cid}_ready_hour", f"{cid}_target_soc", f"{cid}_hours"):
                self.hold_ev(cid)
            if key == f"{cid}_ready_hour":
                self._refresh_manual_expiry(cid)
        self.recalculate()
        if key == S_HEATING_MAX_OUTDOOR:
            await self.async_apply()        # pause/start varmestyringen med det samme

    async def async_set_ev_force(self, cid: str, on: bool) -> None:
        self.ev_force[cid] = on
        self._ev_hold.pop(cid, None)          # "Lad nu" skal starte med det samme
        self.recalculate()
        await self.async_apply(force=True)

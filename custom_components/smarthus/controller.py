"""SmartHus-styring: planlægger og styrer enhederne hvert minut."""

from __future__ import annotations

import logging
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
    API_URL,
    EDS_DAYAHEAD_FROM,
    EDS_OLD_URL,
    CONF_SALE_FEE,
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
    S_SOLAR_SELL_MARGIN,
    S_BATTERY_MAX_SOC,
    BATTERY_SCRIPTS,
    CONF_HUAWEI_DEVICE,
    CONF_HUAWEI_POWER,
    CONF_BATTERY_POWER,
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
    CONF_EXPORT_ENERGY,
    CONF_IMPORT_ENERGY,
    CONF_HEATING_CLIMATE,
    CONF_HEATPUMP_ENERGY,
    CONF_OUTDOOR_TEMP,
    CONF_WEATHER,
    HEATING_OUTDOOR_HYSTERESIS,
    S_HEATING_MAX_OUTDOOR,
    CONF_HOTWATER_ENERGY,
    CONF_HOTWATER_ENTITY,
    CONF_HOTWATER_TEMP,
    CONF_GRID_POWER,
    CONF_GRID_POWER_SIGN,
    SIGN_IMPORT_POSITIVE,
    SURPLUS_CONFIRM_TICKS,
    TICK_SECONDS,
    CONF_SOLAR_POWER,
    F_HOTWATER_SURPLUS_SMART,
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
from .planner import CarInput, Plan, build_plan, car_settings, heating_outdoor_ok
from .profile import (EFFICIENCY, LEARN_DAYS, SUN_END, SUN_START, Profile, Reserve, build_profile, calc_reserve,
                      expected_load, grid_charge_target, solar_surplus)
from .savings import SavingsCalculator
from .flow import energy_flow, to_kw
from .plug import pick_connected, plug_state
from .solar import EvSolarState, regulate_ev
from .weather import forecast_sensor_ids, merge_wh, pick_weather_entity, summarize_forecast

_LOGGER = logging.getLogger(__name__)

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
        self.profile = Profile()
        self.profile_updated: datetime | None = None
        self.reserve: Reserve | None = None
        self.reserve_holds = False  # True når salg fra batteriet er sat på pause for at gemme reserven
        self.sell_solar: dict | None = None  # beslutning om at sælge solstrøm i stedet for at lade batteriet
        self.grid_charge: dict | None = None  # hvor meget der lades fra nettet, når solen kan fylde resten
        self._unsub_learn = None
        self._unsub_learn_first = None
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.savings")
        self._last_cmd: dict[str, float] = {}
        # Sidste tilstand der er sendt via script (scripts køres kun ved skift)
        self._script_state: dict[str, str] = {}
        # Varmt vand med temperaturføler: True når øvre grænse er nået
        self.hotwater_full = False
        self.heating_outdoor_ok = True     # varmestyringen er aktiv ift. udetemperaturen
        self._heating_paused_applied = False  # grundtemperaturen er sat for den nuværende pause
        self._hw_button_on = False
        # Fælles energimåler for varme og varmt vand: fordeles mellem de to
        self._heat_last: float | None = None
        self._heat_cum = {"heatpump": 0.0, "hotwater": 0.0}         # varmt vand via knap: trykket i den nuværende periode
        # Solprognose (Wh pr. periode) fra Forecast.Solar/Solcast – hentes hvert 15. minut
        self._forecast_wh: dict[str, float] = {}
        self._forecast_key_is_end = False
        self._forecast_fetched = 0.0
        self._fc_ids: dict[str, str] = {}
        # Opvarmning med solcelleoverskud
        self.hotwater_surplus = False
        self._surplus_count = 0
        self._hw_surplus_sec = 0.0
        self._hw_done_date: str | None = None   # dag hvor vandet er varmet op
        # Indstillinger der slås til/fra på kortet (gendannes af kontakterne)
        self.flags: dict[str, bool] = {F_HOTWATER_SURPLUS_SMART: True}
        # Solcelleladning pr. elbil
        self.ev_solar: dict[str, EvSolarState] = {}
        self._ev_current_sent: dict[str, int] = {}
        self._unsub = None
        self._unsub_prices = None
        self.expected_unique_ids: set[str] = set()

    # --- Livscyklus --------------------------------------------------------
    async def async_load_savings(self) -> None:
        """Hent gemte besparelser – før sensorerne oprettes, så de ikke kortvarigt viser 0."""
        self.savings = SavingsCalculator(await self._store.async_load())
        self._savings_loaded = True

    async def async_start(self) -> None:
        if not self._savings_loaded:
            await self.async_load_savings()
        self._unsub = async_track_time_change(self.hass, self._async_tick, second=[5, 35])
        self._unsub_prices = self.coordinator.async_add_listener(self._on_prices)
        self._unsub_sl = None
        if self.coordinator.uses_stromligning:
            self._unsub_sl = async_track_state_change_event(
                self.hass, [*self.coordinator.sl_entities, self.coordinator.sl_spot], lambda _e: self.coordinator.async_refresh_external()
            )
        if self.has(CONF_HOUSE_ENERGY):
            # Lær forbrugsmønsteret hver nat og kort efter opstart (når historikken er klar)
            self._unsub_learn = async_track_time_change(self.hass, self.async_learn, hour=0, minute=20, second=30)
            self._unsub_learn_first = async_call_later(self.hass, 60, self.async_learn)
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

        meters = {n: self.config.get(k) for n, k in (("solar", CONF_SOLAR_ENERGY), ("export", CONF_EXPORT_ENERGY),
                                                     ("import", CONF_IMPORT_ENERGY)) if self.config.get(k)}
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
        stats = await rec.async_add_executor_job(
            statistics_during_period, self.hass, start, end, set(meters.values()), "hour",
            {"energy": "kWh"}, {"change", "state"},
        )
        price_ids = set()
        sl_price = sl_spot = None
        if self.coordinator.uses_stromligning:
            sl_price, sl_spot = self.coordinator.sl_entities[0], self.coordinator.sl_spot
            price_ids = {e for e in (sl_price, sl_spot) if e}
        pstats = await rec.async_add_executor_job(
            statistics_during_period, self.hass, start, end, price_ids, "hour", None, {"mean", "state"},
        ) if price_ids else {}

        def ts(row) -> datetime:
            st = row.get("start")
            return datetime.fromtimestamp(st, self.tz) if isinstance(st, (int, float)) else dt_util.as_local(st)

        no_stats = [e for e in meters.values() if not stats.get(e)]
        if no_stats:
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
            raise ValueError("Ingen historik fundet for de valgte energimålere")
        if not start_date:
            # Start først, når alle målere har historik – ellers ser fx timer uden eksportmåler
            # ud som 100 % egetforbrug
            firsts = [ts(rows[0]) for rows in (stats.get(e, []) for e in meters.values()) if rows]
            begin = max(firsts)
            hours = {t: v for t, v in hours.items() if t >= begin}

        def means(entity_id) -> dict[datetime, float]:
            # Pris-sensorer med state_class "total" har ingen middelværdi – brug så timens pris
            out = {}
            for r in pstats.get(entity_id, []):
                v = r.get("mean") if r.get("mean") is not None else r.get("state")
                if v is not None:
                    out[ts(r)] = float(v)
            return out

        buy, spot = means(sl_price), means(sl_spot)
        spot_vat = bool(sl_spot and sl_spot.endswith("_vat"))
        fee = float(self.config.get(CONF_SALE_FEE, DEFAULT_PRICES[CONF_SALE_FEE]))
        missing = sorted(t for t in hours if t not in buy or t not in spot)
        eds = await self._async_eds_hourly_spot(missing[0], missing[-1]) if missing else {}

        rows = []
        for t, v in hours.items():
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
            rows.append({"start": t, "produced": v.get("solar"), "export": v.get("export"),
                         "import": v.get("import"), "price": price, "sale": sale})
        summary = self.savings.rebuild_solar(rows, dt_util.now(), last)
        await self._store.async_save(self.savings.data)
        async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))
        _LOGGER.info("SmartHus: solcellehistorik genberegnet: %s", summary)
        return summary

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
        start = end - timedelta(days=LEARN_DAYS)
        ev_ids = {c["energy"] for c in self.cars if c.get("energy")}
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, start, end, {house} | ev_ids, "hour",
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
        self.profile = build_profile(sorted(house_hours.items()))
        self.profile_updated = dt_util.now()
        if not self.profile.ready:
            _LOGGER.info("SmartHus: kun %s dage med historik for %s – reserven bruges fra 2 dage",
                         self.profile.days, house)
        self.recalculate()

    def _reserve_until(self, now: datetime) -> datetime:
        """Hvornår bliver strømmen billig igen? Næste planlagte opladning, ellers næste billige tidsrum."""
        charge = [s.start for s in self.plan.battery_charge if s.start > now]
        if charge:
            return min(charge)
        future = [s for s in (self.coordinator.data or []) if s.start > now]
        if future:
            prices = sorted(s.price for s in future)
            lo = prices[len(prices) // 4]
            cheap = [s.start for s in future if s.price <= lo]
            if cheap:
                return min(cheap)
        local = now.astimezone(self.tz)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(now.tzinfo)

    def _update_reserve(self) -> None:
        self.reserve, self.reserve_holds = None, False
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
            solar_remaining=self._energy_kwh(CONF_SOLAR_FORECAST) if self.has(CONF_SOLAR_FORECAST)
            else self.solar_forecast.get("remaining_kwh"),
        )
        # Sælg kun det, der er ud over reserven
        if self.plan.battery_action == ACTION_DISCHARGE and self.reserve.sellable_kwh is not None \
                and self.reserve.sellable_kwh <= 0.05:
            self.plan.battery_action = ACTION_NORMAL
            self.reserve_holds = True
        self._decide_grid_charge(now)
        self._decide_sell_solar(now)

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
        surplus = solar_surplus(self.profile, day, kwh, from_h)
        sun_at = day.replace(hour=min(max(from_h, SUN_START) + 3, 23), minute=0, second=0, microsecond=0)
        until_sun = expected_load(self.profile, local, max(sun_at, local))
        cap, mx, mn = self.settings[S_BATTERY_CAPACITY], self.settings[S_BATTERY_MAX_SOC], self.settings[S_BATTERY_MIN_SOC]
        target = grid_charge_target(capacity=cap, min_soc=mn, max_soc=mx, surplus_kwh=surplus, until_sun_kwh=until_sun)
        # Er salg af solstrøm midt på dagen bedre end at bruge den selv? Så lades der fuldt fra nettet.
        mid = [s for s in (self.coordinator.data or [])
               if s.start.astimezone(self.tz).date() == day.date() and 10 <= s.start.astimezone(self.tz).hour < 15]
        sale_mid = sum(s.sale for s in mid) / len(mid) if mid else None
        margin = float(self.settings.get(S_SOLAR_SELL_MARGIN, 0.10))
        sell_better = (self.has_sell_solar and sale_mid is not None
                       and sale_mid - p.current.price / EFFICIENCY >= margin)
        if sell_better:
            target = cap * mx / 100
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
            p.battery_action = ACTION_NORMAL

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

    async def async_stop(self) -> None:
        for name in ("_unsub_learn", "_unsub_learn_first"):
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
        await self._store.async_save(self.savings.data)

    @callback
    def _on_prices(self) -> None:
        self.recalculate()

    # --- Hjælpere ----------------------------------------------------------
    def has(self, key: str) -> bool:
        # Batteri og eksport tæller som opsat, hvis der enten er valgt en entitet eller scripts
        if key == CONF_BATTERY_MODE_ENTITY:
            return self.has_battery_control
        if key == CONF_SOLAR_CURTAIL:
            return self.has_export_control
        if key == "battery_sell_solar":
            return self.has_sell_solar
        return bool(self.config.get(key))

    @property
    def has_battery_control(self) -> bool:
        return (bool(self.config.get(CONF_BATTERY_MODE_ENTITY)) or bool(self.config.get(CONF_HUAWEI_DEVICE))
                or any(self.config.get(k) for k in BATTERY_SCRIPTS))

    @property
    def has_sell_solar(self) -> bool:
        """Kan batteriet sættes til at sælge solstrømmen i stedet for at lade? (valgfri tilstand/script)"""
        return bool(self.config.get(CONF_BATTERY_SCRIPT_SELL_SOLAR)) or (
            bool(self.config.get(CONF_BATTERY_MODE_ENTITY)) and bool(self.config.get(CONF_BATTERY_OPT_SELL_SOLAR)))

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
                     soc=self._float_state(car.get("soc")), force=self.ev_force.get(car["id"], False),
                     connected=self.connected.get(car["id"], True))
            for car in self.cars
        ]
        self.plan = build_plan(slots, dt_util.utcnow(), self.tz, self.settings, soc=soc, cars=cars)
        for cid, ep in self.plan.evs.items():
            if self.ev_force.get(cid) and ep.target_reached:
                self.ev_force[cid] = False  # "Lad nu" slukker selv ved mål-SoC
        self._update_reserve()
        if self.has(CONF_HEATING_CLIMATE):
            self._update_heating_outdoor()   # så kortet viser pause/aktiv med det samme
        async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))

    async def _async_tick(self, now: datetime) -> None:
        if time.monotonic() - self._forecast_fetched > 900 or not self._forecast_fetched:
            self._forecast_fetched = time.monotonic()
            await self._async_refresh_forecast()
        if self.active(MODULE_HOTWATER):
            self._update_surplus()
        self.recalculate()
        await self.async_apply()
        self._update_savings()

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
        self.savings.update(dt_util.now(), self.plan.current, self.plan.day_avg, readings,
                            battery_action=action, battery_kwh=bat_kwh)
        self._store.async_delay_save(lambda: self.savings.data, 60)

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

    async def _ensure_switch(self, entity_id: str, on: bool, force: bool = False) -> None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == STATE_UNAVAILABLE:
            return
        if (st.state == STATE_ON) == on:
            return
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
               ACTION_SELL_SOLAR: CONF_BATTERY_SCRIPT_SELL_SOLAR}.get(action, CONF_BATTERY_SCRIPT_NORMAL)
        if await self._run_script(key, "battery", action, force, self._script_vars(action)):
            return
        if self.config.get(CONF_HUAWEI_DEVICE):
            await self._apply_huawei(action, force)
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

    async def _apply_huawei(self, action: str, force: bool) -> None:
        """Huawei (huawei_solar): tvungen opladning/afladning via integrationens egne handlinger."""
        device = self.config[CONF_HUAWEI_DEVICE]
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

    def _battery_option(self, action: str) -> str:
        key = {
            ACTION_CHARGE: CONF_BATTERY_OPT_CHARGE,
            ACTION_DISCHARGE: CONF_BATTERY_OPT_DISCHARGE,
            ACTION_SELL_SOLAR: CONF_BATTERY_OPT_SELL_SOLAR,
        }.get(action, CONF_BATTERY_OPT_NORMAL)
        if key == CONF_BATTERY_OPT_SELL_SOLAR:
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

    async def _apply_battery_step(self, p, force: bool) -> None:
        if self.active(MODULE_BATTERY) and self.has_battery_control:
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
                await self._ensure_switch(car["charger"], on, force)
                if on and current is not None:
                    await self._apply_current(car, current, force)

    async def _apply_hotwater_step(self, p, force: bool) -> None:
        if self.active(MODULE_HOTWATER) and self.has(CONF_HOTWATER_ENTITY):
            await self._apply_hotwater(p.hotwater_on, force)

    async def _apply_heating_step(self, p, force: bool) -> None:
        if not (self.active(MODULE_HEATING) and self.has(CONF_HEATING_CLIMATE)):
            return
        if not self._update_heating_outdoor():
            if not self._heating_paused_applied or force:
                # sæt grundtemperaturen én gang, og lad så termostaten være
                await self._ensure_temperature(self.config[CONF_HEATING_CLIMATE],
                                               self.settings[S_HEATING_BASE_TEMP], True)
                self._heating_paused_applied = True
            return
        self._heating_paused_applied = False
        base = self.settings[S_HEATING_BASE_TEMP]
        temp = base
        if p.heating_mode == HEATING_BOOST:
            temp = base + self.settings[S_HEATING_BOOST_DEG]
        elif p.heating_mode == HEATING_REDUCE:
            temp = base - self.settings[S_HEATING_REDUCE_DEG]
        await self._ensure_temperature(self.config[CONF_HEATING_CLIMATE], temp, force)

    def _plug(self, car: dict) -> bool | None:
        entity_id = car.get("plug")
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        return plug_state(st.state if st else None)

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
        st = self.ev_solar.setdefault(cid, EvSolarState())
        control = self.has_current_control(car)
        solar_on = control and self.flags.get(f"{cid}_solar", False) and self.export_kw is not None
        if not solar_on or ep.target_reached:
            st.reset()
            return ep.on, (max_a if control and ep.on and not ep.target_reached else None)
        if ep.on or self.plan.solar_curtail:
            # Billig time, "Lad nu" eller eksporten er stoppet: lad med fuld strøm
            st.reset()
            return True, max_a
        if solar_car not in (None, cid):
            st.reset()                   # en anden bil lader allerede med sol
            return False, None
        if step:  # kun én reguleringsrunde pr. 30 sekunder
            regulate_ev(st, self.export_kw, self.solar_kw, min_a, max_a, cs["solar_start_kw"])
        return (True, st.current) if st.active else (False, None)

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

    def _power_kw(self, entity_id: str | None) -> float | None:
        val = self._float_state(entity_id)
        if val is None:
            return None
        return to_kw(val, self.hass.states.get(entity_id).attributes.get("unit_of_measurement"))

    @property
    def battery_kw(self) -> float | None:
        """Batteriets effekt lige nu i kW (positiv = afladning)."""
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
        f["has_battery"] = self.has_battery_control or bool(self.config.get(CONF_BATTERY_POWER))
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
        """Hent timeprognosen fra Forecast.Solar eller Solcast (samme data som energi-dashboardet)."""
        try:
            from homeassistant.loader import async_get_integration
        except ImportError:
            return
        for domain, key_is_end in (("forecast_solar", True), ("solcast_solar", False)):
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
        out = summarize_forecast(self._forecast_wh, dt_util.utcnow(), self.tz, self._forecast_key_is_end) \
            if self._forecast_wh else {}
        ids = self._forecast_ids()
        # Sensorerne er det, brugeren kender – de vinder over vores egen sum
        for key, out_key in (("today", "today_kwh"), ("remaining", "remaining_kwh"), ("tomorrow", "tomorrow_kwh")):
            val = self._energy_entity_kwh(ids.get(key))
            if val is not None:
                out[out_key] = round(val, 2)
        power = self._power_kw(ids.get("power_now"))
        if power is not None:
            out["power_now_kw"] = round(power, 2)
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
    def hotwater_temp(self) -> float | None:
        return self._float_state(self.config.get(CONF_HOTWATER_TEMP))

    def _hotwater_switch_on(self, planned_on: bool) -> bool:
        """Med temperaturføler: sluk ved øvre grænse, start igen når vandet er kølet af."""
        if planned_on and not self.hotwater_surplus and self.hotwater_heated_today:
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
            await self._ensure_switch(self.car(module)["charger"], True, True)
        elif module == MODULE_HOTWATER and self.has(CONF_HOTWATER_ENTITY):
            entity_id = self.config[CONF_HOTWATER_ENTITY]
            if entity_id.split(".")[0] in ("water_heater", "climate"):
                await self._ensure_temperature(entity_id, self.settings[S_HOTWATER_NORMAL_TEMP], True)
            elif entity_id.split(".")[0] in ("button", "input_button"):
                self._hw_button_on = False     # varmepumpen styrer selv igen
            else:
                await self._ensure_switch(entity_id, True, True)
        elif module == MODULE_HEATING and self.has(CONF_HEATING_CLIMATE):
            await self._ensure_temperature(self.config[CONF_HEATING_CLIMATE],
                                           self.settings[S_HEATING_BASE_TEMP], True)

    async def async_set_enabled(self, module: str, on: bool) -> None:
        was = self.active(module) if module != MODULE_MASTER else self.enabled[MODULE_MASTER]
        self.enabled[module] = on
        if module == MODULE_MASTER:
            if was and not on:
                for m in self.modules:
                    if self.enabled[m]:
                        await self.async_restore_normal(m)
        elif was and not on:
            await self.async_restore_normal(module)
        self.recalculate()
        if on:
            await self.async_apply(force=True)

    async def async_set_setting(self, key: str, value: float) -> None:
        self.settings[key] = value
        self.recalculate()
        if key == S_HEATING_MAX_OUTDOOR:
            await self.async_apply()        # pause/start varmestyringen med det samme

    async def async_set_ev_force(self, cid: str, on: bool) -> None:
        self.ev_force[cid] = on
        self.recalculate()
        await self.async_apply(force=True)

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
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_NORMAL,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_BATTERY_MODE_ENTITY,
    CONF_BATTERY_OPT_CHARGE,
    CONF_BATTERY_OPT_DISCHARGE,
    CONF_BATTERY_OPT_NORMAL,
    CONF_BATTERY_SOC,
    CONF_EXPORT_ENERGY,
    CONF_HEATING_CLIMATE,
    CONF_HEATPUMP_ENERGY,
    CONF_HOTWATER_ENERGY,
    CONF_HOTWATER_ENTITY,
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
from .planner import CarInput, Plan, build_plan
from .profile import LEARN_DAYS, Profile, Reserve, build_profile, calc_reserve
from .savings import SavingsCalculator

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
        self.plan = Plan()
        self.savings = SavingsCalculator()
        self.profile = Profile()
        self.profile_updated: datetime | None = None
        self.reserve: Reserve | None = None
        self.reserve_holds = False  # True når salg fra batteriet er sat på pause for at gemme reserven
        self._unsub_learn = None
        self._unsub_learn_first = None
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.savings")
        self._last_cmd: dict[str, float] = {}
        self._unsub = None
        self._unsub_prices = None
        self.expected_unique_ids: set[str] = set()

    # --- Livscyklus --------------------------------------------------------
    async def async_start(self) -> None:
        stored = await self._store.async_load()
        self.savings = SavingsCalculator(stored)
        self._unsub = async_track_time_change(self.hass, self._async_tick, second=5)
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
        if not self.has(CONF_HOUSE_ENERGY) or not self.has(CONF_BATTERY_MODE_ENTITY) or not self.profile.ready:
            return
        now = dt_util.utcnow()
        until = min(self._reserve_until(now), now + timedelta(hours=24))
        self.reserve = calc_reserve(
            self.profile, now.astimezone(self.tz), until.astimezone(self.tz),
            soc=self._float_state(self.config.get(CONF_BATTERY_SOC)),
            capacity=self.settings[S_BATTERY_CAPACITY], min_soc=self.settings[S_BATTERY_MIN_SOC],
            solar_remaining=self._energy_kwh(CONF_SOLAR_FORECAST) if self.has(CONF_SOLAR_FORECAST) else None,
        )
        # Sælg kun det, der er ud over reserven
        if self.plan.battery_action == ACTION_DISCHARGE and self.reserve.sellable_kwh is not None \
                and self.reserve.sellable_kwh <= 0.05:
            self.plan.battery_action = ACTION_NORMAL
            self.reserve_holds = True

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
        return bool(self.config.get(key))

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
        cars = [
            CarInput(car["id"], car.get("name") or "Elbil",
                     soc=self._float_state(car.get("soc")), force=self.ev_force.get(car["id"], False))
            for car in self.cars
        ]
        self.plan = build_plan(slots, dt_util.utcnow(), self.tz, self.settings, soc=soc, cars=cars)
        for cid, ep in self.plan.evs.items():
            if self.ev_force.get(cid) and ep.target_reached:
                self.ev_force[cid] = False  # "Lad nu" slukker selv ved mål-SoC
        self._update_reserve()
        async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))

    async def _async_tick(self, now: datetime) -> None:
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
        }
        for name, key in mapping.items():
            if self.has(key):
                readings[name] = self._energy_kwh(key)
        self.savings.update(dt_util.now(), self.plan.current, self.plan.day_avg, readings)
        self._store.async_delay_save(lambda: self.savings.data, 60)

    # --- Styring -----------------------------------------------------------
    def _rate_ok(self, entity_id: str) -> bool:
        last = self._last_cmd.get(entity_id, 0)
        return time.monotonic() - last >= MIN_COMMAND_INTERVAL

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

    def _battery_option(self, action: str) -> str:
        key = {
            ACTION_CHARGE: CONF_BATTERY_OPT_CHARGE,
            ACTION_DISCHARGE: CONF_BATTERY_OPT_DISCHARGE,
        }.get(action, CONF_BATTERY_OPT_NORMAL)
        return self.config.get(key) or DEFAULT_BATTERY_OPTIONS[key]

    async def async_apply(self, force: bool = False) -> None:
        """Bring enhederne i den tilstand planen ønsker."""
        p = self.plan
        if p.current is None:
            return
        if self.active(MODULE_BATTERY) and self.has(CONF_BATTERY_MODE_ENTITY):
            await self._ensure_option(self.config[CONF_BATTERY_MODE_ENTITY],
                                      self._battery_option(p.battery_action), force)
        if self.active(MODULE_SOLAR) and self.has(CONF_SOLAR_CURTAIL):
            await self._ensure_switch(self.config[CONF_SOLAR_CURTAIL], p.solar_curtail, force)
        for car in self.cars:
            ep = p.evs.get(car["id"])
            if ep and self.active(car["id"]):
                await self._ensure_switch(car["charger"], ep.on, force)
        if self.active(MODULE_HOTWATER) and self.has(CONF_HOTWATER_ENTITY):
            await self._apply_hotwater(p.hotwater_on, force)
        if self.active(MODULE_HEATING) and self.has(CONF_HEATING_CLIMATE):
            base = self.settings[S_HEATING_BASE_TEMP]
            temp = base
            if p.heating_mode == HEATING_BOOST:
                temp = base + self.settings[S_HEATING_BOOST_DEG]
            elif p.heating_mode == HEATING_REDUCE:
                temp = base - self.settings[S_HEATING_REDUCE_DEG]
            await self._ensure_temperature(self.config[CONF_HEATING_CLIMATE], temp, force)

    async def _apply_hotwater(self, on: bool, force: bool) -> None:
        entity_id = self.config[CONF_HOTWATER_ENTITY]
        if entity_id.split(".")[0] in ("water_heater", "climate"):
            temp = self.settings[S_HOTWATER_BOOST_TEMP if on else S_HOTWATER_NORMAL_TEMP]
            await self._ensure_temperature(entity_id, temp, force)
        else:
            await self._ensure_switch(entity_id, on, force)

    async def async_restore_normal(self, module: str) -> None:
        """Sæt et modul tilbage til normal drift, når styringen slås fra."""
        if module == MODULE_BATTERY and self.has(CONF_BATTERY_MODE_ENTITY):
            await self._ensure_option(self.config[CONF_BATTERY_MODE_ENTITY],
                                      self._battery_option(ACTION_NORMAL), True)
        elif module == MODULE_SOLAR and self.has(CONF_SOLAR_CURTAIL):
            await self._ensure_switch(self.config[CONF_SOLAR_CURTAIL], False, True)
        elif module in self.car_ids:
            await self._ensure_switch(self.car(module)["charger"], True, True)
        elif module == MODULE_HOTWATER and self.has(CONF_HOTWATER_ENTITY):
            entity_id = self.config[CONF_HOTWATER_ENTITY]
            if entity_id.split(".")[0] in ("water_heater", "climate"):
                await self._ensure_temperature(entity_id, self.settings[S_HOTWATER_NORMAL_TEMP], True)
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

    async def async_set_ev_force(self, cid: str, on: bool) -> None:
        self.ev_force[cid] = on
        self.recalculate()
        await self.async_apply(force=True)

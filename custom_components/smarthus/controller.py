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
    CONF_BATTERY_SCRIPT_CHARGE,
    CONF_BATTERY_SCRIPT_DISCHARGE,
    CONF_BATTERY_SCRIPT_NORMAL,
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
    CONF_HEATING_CLIMATE,
    CONF_HEATPUMP_ENERGY,
    CONF_OUTDOOR_TEMP,
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
from .profile import LEARN_DAYS, Profile, Reserve, build_profile, calc_reserve
from .savings import SavingsCalculator
from .flow import energy_flow, to_kw
from .plug import pick_connected, plug_state
from .solar import EvSolarState, regulate_ev

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
        self.profile = Profile()
        self.profile_updated: datetime | None = None
        self.reserve: Reserve | None = None
        self.reserve_holds = False  # True når salg fra batteriet er sat på pause for at gemme reserven
        self._unsub_learn = None
        self._unsub_learn_first = None
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.savings")
        self._last_cmd: dict[str, float] = {}
        # Sidste tilstand der er sendt via script (scripts køres kun ved skift)
        self._script_state: dict[str, str] = {}
        # Varmt vand med temperaturføler: True når øvre grænse er nået
        self.hotwater_full = False
        self.heating_outdoor_ok = True     # varmestyringen er aktiv ift. udetemperaturen
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
    async def async_start(self) -> None:
        stored = await self._store.async_load()
        self.savings = SavingsCalculator(stored)
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
        # Batteri og eksport tæller som opsat, hvis der enten er valgt en entitet eller scripts
        if key == CONF_BATTERY_MODE_ENTITY:
            return self.has_battery_control
        if key == CONF_SOLAR_CURTAIL:
            return self.has_export_control
        return bool(self.config.get(key))

    @property
    def has_battery_control(self) -> bool:
        return (bool(self.config.get(CONF_BATTERY_MODE_ENTITY)) or bool(self.config.get(CONF_HUAWEI_DEVICE))
                or any(self.config.get(k) for k in BATTERY_SCRIPTS))

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
        async_dispatcher_send(self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id))

    async def _async_tick(self, now: datetime) -> None:
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
        }
        for name, key in mapping.items():
            if self.has(key):
                readings[name] = self._energy_kwh(key)
        self.savings.update(dt_util.now(), self.plan.current, self.plan.day_avg, readings)
        self._store.async_delay_save(lambda: self.savings.data, 60)

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
        key = {ACTION_CHARGE: CONF_BATTERY_SCRIPT_CHARGE, ACTION_DISCHARGE: CONF_BATTERY_SCRIPT_DISCHARGE}.get(
            action, CONF_BATTERY_SCRIPT_NORMAL)
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
        }.get(action, CONF_BATTERY_OPT_NORMAL)
        return self.config.get(key) or DEFAULT_BATTERY_OPTIONS[key]

    async def async_apply(self, force: bool = False) -> None:
        """Bring enhederne i den tilstand planen ønsker."""
        p = self.plan
        if p.current is None:
            return
        if self.active(MODULE_BATTERY) and self.has_battery_control:
            await self._apply_battery(p.battery_action, force)
        if self.active(MODULE_SOLAR) and self.has_export_control:
            await self._apply_export(p.solar_curtail, force)
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
        if self.active(MODULE_HOTWATER) and self.has(CONF_HOTWATER_ENTITY):
            await self._apply_hotwater(p.hotwater_on, force)
        if self.active(MODULE_HEATING) and self.has(CONF_HEATING_CLIMATE):
            was_ok = self.heating_outdoor_ok
            if not self._update_heating_outdoor():
                if was_ok or force:     # sæt grundtemperaturen én gang, og lad så termostaten være
                    await self._ensure_temperature(self.config[CONF_HEATING_CLIMATE],
                                                   self.settings[S_HEATING_BASE_TEMP], True)
                return
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

    @property
    def outdoor_temp(self) -> float | None:
        return self._float_state(self.config.get(CONF_OUTDOOR_TEMP))

    def _update_heating_outdoor(self) -> bool:
        """Varmestyringen er kun aktiv, når det er koldere ude end grænsen (med 1 °C hysterese).
        Uden udeføler – eller hvis føleren ikke svarer – beholdes den seneste tilstand."""
        if not self.has(CONF_OUTDOOR_TEMP):
            self.heating_outdoor_ok = True
            return True
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

"""Opsætning af SmartHus via brugerfladen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .pricing import parse_price_list

from .const import (
    AREAS,
    BATTERY_KEYS,
    CONF_AREA,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_BATTERY_MODE_ENTITY,
    CONF_BATTERY_SCRIPT_CHARGE,
    CONF_BATTERY_SCRIPT_DISCHARGE,
    CONF_BATTERY_SCRIPT_NORMAL,
    CONF_HUAWEI_DEVICE,
    CONF_HUAWEI_POWER,
    CONF_BATTERY_POWER,
    CONF_BATTERY_POWER_SIGN,
    SIGN_CHARGE_POSITIVE,
    SIGN_DISCHARGE_POSITIVE,
    CONF_EXPORT_OPT_NORMAL,
    CONF_EXPORT_OPT_STOP,
    CONF_EXPORT_SCRIPT_START,
    CONF_EXPORT_SCRIPT_STOP,
    CONF_EXPORT_VALUE_NORMAL,
    CONF_EXPORT_VALUE_STOP,
    CONF_BATTERY_OPT_CHARGE,
    CONF_BATTERY_OPT_DISCHARGE,
    CONF_BATTERY_OPT_NORMAL,
    CONF_BATTERY_SOC,
    CONF_ELAFGIFT,
    CONF_ENERGINET,
    CONF_EV_ENERGY,
    CONF_EV_SOC,
    CONF_EV_SWITCH,
    CONF_EXPORT_ENERGY,
    CONF_HEATING_CLIMATE,
    CONF_HEATPUMP_ENERGY,
    CONF_HOTWATER_ENERGY,
    CONF_HOTWATER_TEMP,
    CONF_SOLAR_POWER,
    CONF_GRID_POWER,
    CONF_GRID_POWER_SIGN,
    SIGN_EXPORT_POSITIVE,
    SIGN_IMPORT_POSITIVE,
    CONF_HOUSE_ENERGY,
    CONF_SOLAR_FORECAST,
    CONF_HOTWATER_ENTITY,
    CONF_MARKUP,
    CONF_NAME,
    CONF_PRICE_SOURCE,
    CONF_SALE_FEE,
    CONF_SL_PRICE,
    CONF_SL_SPOT,
    CONF_SL_TOMORROW,
    DEFAULT_SL_PRICE,
    DEFAULT_SL_SPOT,
    DEFAULT_SL_TOMORROW,
    SOURCE_EDS,
    SOURCE_STROMLIGNING,
    CONF_SOLAR_CURTAIL,
    CONF_SOLAR_ENERGY,
    CONF_T_SUMMER_HIGH,
    CONF_T_SUMMER_LOW,
    CONF_T_SUMMER_PEAK,
    CONF_T_WINTER_HIGH,
    CONF_T_WINTER_LOW,
    CONF_T_WINTER_PEAK,
    CONF_VAT,
    DEFAULT_BATTERY_OPTIONS,
    DEFAULT_PRICES,
    DOMAIN,
    CONF_EVS,
    EV_KEYS,
    get_cars,
    HEATING_KEYS,
    HOTWATER_KEYS,
    PRICE_KEYS,
    SOLAR_KEYS,
)

# Prisområder vises med navn (oversættelsesnøgler må ikke have store bogstaver, så navnene står her)
AREA_OPTIONS = [
    selector.SelectOptionDict(value="DK1", label="DK1 – Vestdanmark (Jylland og Fyn)"),
    selector.SelectOptionDict(value="DK2", label="DK2 – Østdanmark (Sjælland og øerne)"),
]

KR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=-5, max=10, step=0.001, mode=selector.NumberSelectorMode.BOX,
                                  unit_of_measurement="kr/kWh")
)
TEXT = selector.TextSelector()
ENERGY = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy"))
SOC = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"]))
MODE = selector.EntitySelector(selector.EntitySelectorConfig(domain=["select", "input_select"]))
ONOFF = selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "input_boolean"]))
SCRIPT = selector.EntitySelector(selector.EntitySelectorConfig(domain="script"))
EXPORT = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["switch", "input_boolean", "select", "input_select", "number", "input_number"])
)
EXPORT_VALUE = selector.NumberSelector(
    selector.NumberSelectorConfig(min=-100000, max=100000, step="any", mode=selector.NumberSelectorMode.BOX)
)
HOTWATER = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["switch", "input_boolean", "water_heater", "climate"])
)
POWER = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power"))
TEMP = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "input_number"]))
CLIMATE = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))
FORECAST = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


NEW_CAR = "__new__"
CAR_CHOICE = "car"
CAR_REMOVE = "remove"
CAR_NAME = "car_name"
CAR_CURRENT_ENTITY = "ev_current_entity"
CAR_CURRENT_SCRIPT = "ev_current_script"
HUAWEI_DEVICE = selector.DeviceSelector(selector.DeviceSelectorConfig(integration="huawei_solar"))
HUAWEI_POWER = selector.NumberSelector(selector.NumberSelectorConfig(min=100, max=20000, step=100,
                                                                    mode=selector.NumberSelectorMode.BOX,
                                                                    unit_of_measurement="W"))
EASEE_DEVICE = selector.DeviceSelector(selector.DeviceSelectorConfig(integration="easee"))
CAR_EASEE_DEVICE = "ev_easee_device"
CAR_PLUG = "ev_plug_sensor"
CAR_POWER = "ev_power_sensor"
PLUG = selector.EntitySelector(selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"]))
CURRENT = selector.EntitySelector(selector.EntitySelectorConfig(domain=["number", "input_number"]))


def _new_car_id(name: str, cars: list[dict]) -> str:
    """Første bil hedder 'ev' (så de oprindelige entity-id'er bevares), de næste fx 'ev_tesla'."""
    taken = {c["id"] for c in cars}
    if "ev" not in taken:
        return "ev"
    base = f"ev_{slugify(name) or 'bil'}"
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}_{n}", n + 1
    return cid


def _price_schema(values: dict) -> vol.Schema:
    v = {**DEFAULT_PRICES, CONF_PRICE_SOURCE: SOURCE_EDS, **values}
    return vol.Schema(
        {
            vol.Required(CONF_PRICE_SOURCE, default=v[CONF_PRICE_SOURCE]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[SOURCE_EDS, SOURCE_STROMLIGNING], translation_key="price_source",
                                              mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Optional(CONF_SL_PRICE, description={"suggested_value": v.get(CONF_SL_PRICE) or DEFAULT_SL_PRICE}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_SL_TOMORROW, description={"suggested_value": v.get(CONF_SL_TOMORROW) or DEFAULT_SL_TOMORROW}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])),
            vol.Optional(CONF_SL_SPOT, description={"suggested_value": v.get(CONF_SL_SPOT) or DEFAULT_SL_SPOT}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_AREA, default=v[CONF_AREA]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=AREA_OPTIONS,
                                              mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Required(CONF_VAT, default=v[CONF_VAT]): selector.BooleanSelector(),
            vol.Required(CONF_MARKUP, default=v[CONF_MARKUP]): KR,
            vol.Required(CONF_ENERGINET, default=v[CONF_ENERGINET]): KR,
            vol.Required(CONF_ELAFGIFT, default=v[CONF_ELAFGIFT]): KR,
            vol.Required(CONF_SALE_FEE, default=v[CONF_SALE_FEE]): KR,
            vol.Required(CONF_T_WINTER_LOW, default=v[CONF_T_WINTER_LOW]): KR,
            vol.Required(CONF_T_WINTER_HIGH, default=v[CONF_T_WINTER_HIGH]): KR,
            vol.Required(CONF_T_WINTER_PEAK, default=v[CONF_T_WINTER_PEAK]): KR,
            vol.Required(CONF_T_SUMMER_LOW, default=v[CONF_T_SUMMER_LOW]): KR,
            vol.Required(CONF_T_SUMMER_HIGH, default=v[CONF_T_SUMMER_HIGH]): KR,
            vol.Required(CONF_T_SUMMER_PEAK, default=v[CONF_T_SUMMER_PEAK]): KR,
        }
    )


def _export_errors(user_input: dict) -> dict:
    """Tjek at en rullemenu har begge tilstande, og at et tal har begge værdier."""
    entity_id = user_input.get(CONF_SOLAR_CURTAIL) or ""
    domain = entity_id.split(".")[0]
    errors: dict[str, str] = {}
    if domain in ("select", "input_select"):
        if not user_input.get(CONF_EXPORT_OPT_STOP) or not user_input.get(CONF_EXPORT_OPT_NORMAL):
            errors[CONF_SOLAR_CURTAIL] = "export_select_options"
    elif domain in ("number", "input_number"):
        if user_input.get(CONF_EXPORT_VALUE_STOP) in (None, "") or user_input.get(CONF_EXPORT_VALUE_NORMAL) in (None, ""):
            errors[CONF_SOLAR_CURTAIL] = "export_number_values"
    if bool(user_input.get(CONF_EXPORT_SCRIPT_STOP)) != bool(user_input.get(CONF_EXPORT_SCRIPT_START)):
        errors[CONF_EXPORT_SCRIPT_STOP] = "export_scripts_pair"
    return errors


def _price_errors(hass, user_input: dict) -> dict:
    """Tjek at Strømligning-sensoren findes og har priser, når den er valgt."""
    if user_input.get(CONF_PRICE_SOURCE) != SOURCE_STROMLIGNING:
        return {}
    entity_id = user_input.get(CONF_SL_PRICE) or DEFAULT_SL_PRICE
    st = hass.states.get(entity_id)
    if st is None:
        return {CONF_SL_PRICE: "stromligning_missing"}
    if not parse_price_list(st.attributes.get("prices")):
        return {CONF_SL_PRICE: "stromligning_no_prices"}
    return {}


def _opt(key: str, current: dict, sel: Any, default: str | None = None) -> tuple:
    value = current.get(key, default)
    return vol.Optional(key, description={"suggested_value": value} if value not in (None, "") else None), sel


def _entity_schema(fields: list[tuple[str, Any]], current: dict, defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(dict(_opt(k, current, s, defaults.get(k)) for k, s in fields))


class SmartHusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Første opsætning: navn, prisområde og tariffer."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_NAME] = user_input[CONF_NAME]
            return await self.async_step_prices()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default="SmartHus"): str}),
        )

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = _price_errors(self.hass, user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            self._data.update(user_input)
            await self.async_set_unique_id(f"{self._data[CONF_NAME].lower()}_{user_input[CONF_AREA]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
        return self.async_show_form(step_id="prices", data_schema=_price_schema(user_input or {}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SmartHusOptionsFlow()


class SmartHusOptionsFlow(OptionsFlow):
    """Tilføj/ret enheder og priser efter installation."""

    _car_id: str = NEW_CAR

    def _current(self) -> dict:
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, keys: list[str], user_input: dict) -> ConfigFlowResult:
        new = dict(self.config_entry.options)
        for key in keys:
            val = user_input.get(key)
            if val in (None, "", []):
                new.pop(key, None)
            else:
                new[key] = val
        return self.async_create_entry(data=new)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["battery", "solar", "ev", "hotwater", "heating", "prices"],
        )

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = _price_errors(self.hass, user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            return self._save(PRICE_KEYS, user_input)
        return self.async_show_form(step_id="prices", data_schema=_price_schema({**self._current(), **(user_input or {})}),
                                    errors=errors)

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(BATTERY_KEYS, user_input)
        fields = [
            (CONF_BATTERY_MODE_ENTITY, MODE),
            (CONF_BATTERY_OPT_CHARGE, TEXT),
            (CONF_BATTERY_OPT_DISCHARGE, TEXT),
            (CONF_BATTERY_OPT_NORMAL, TEXT),
            (CONF_HUAWEI_DEVICE, HUAWEI_DEVICE),
            (CONF_HUAWEI_POWER, HUAWEI_POWER),
            (CONF_BATTERY_SCRIPT_CHARGE, SCRIPT),
            (CONF_BATTERY_SCRIPT_DISCHARGE, SCRIPT),
            (CONF_BATTERY_SCRIPT_NORMAL, SCRIPT),
            (CONF_BATTERY_SOC, SOC),
            (CONF_BATTERY_POWER, POWER),
            (CONF_BATTERY_CHARGE_ENERGY, ENERGY),
            (CONF_BATTERY_DISCHARGE_ENERGY, ENERGY),
            (CONF_HOUSE_ENERGY, ENERGY),
        ]
        cur = self._current()
        schema = _entity_schema(fields, cur, DEFAULT_BATTERY_OPTIONS).extend({
            vol.Optional(CONF_BATTERY_POWER_SIGN, default=cur.get(CONF_BATTERY_POWER_SIGN) or SIGN_DISCHARGE_POSITIVE):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[SIGN_DISCHARGE_POSITIVE, SIGN_CHARGE_POSITIVE], translation_key="battery_power_sign",
                    mode=selector.SelectSelectorMode.LIST)),
        })
        return self.async_show_form(step_id="battery", data_schema=schema)

    async def async_step_solar(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _export_errors(user_input)
            if not errors:
                return self._save(SOLAR_KEYS, user_input)
        fields = [
            (CONF_SOLAR_ENERGY, ENERGY), (CONF_EXPORT_ENERGY, ENERGY), (CONF_SOLAR_FORECAST, FORECAST),
            (CONF_GRID_POWER, POWER), (CONF_SOLAR_POWER, POWER),
            (CONF_SOLAR_CURTAIL, EXPORT),
            (CONF_EXPORT_OPT_STOP, TEXT), (CONF_EXPORT_OPT_NORMAL, TEXT),
            (CONF_EXPORT_VALUE_STOP, EXPORT_VALUE), (CONF_EXPORT_VALUE_NORMAL, EXPORT_VALUE),
            (CONF_EXPORT_SCRIPT_STOP, SCRIPT), (CONF_EXPORT_SCRIPT_START, SCRIPT),
        ]
        cur = {**self._current(), **(user_input or {})}
        schema = _entity_schema(fields, cur).extend({
            vol.Optional(CONF_GRID_POWER_SIGN, default=cur.get(CONF_GRID_POWER_SIGN) or SIGN_EXPORT_POSITIVE):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[SIGN_EXPORT_POSITIVE, SIGN_IMPORT_POSITIVE], translation_key="grid_power_sign",
                    mode=selector.SelectSelectorMode.LIST)),
        })
        return self.async_show_form(step_id="solar", errors=errors, data_schema=schema)

    # --- Elbiler ---------------------------------------------------------
    def _cars(self) -> list[dict]:
        return [dict(c) for c in get_cars(self._current())]

    def _save_cars(self, cars: list[dict]) -> ConfigFlowResult:
        new = dict(self.config_entry.options)
        for key in EV_KEYS:  # ældre opsætning med én bil flyttes over i listen
            new.pop(key, None)
        new[CONF_EVS] = cars
        return self.async_create_entry(data=new)

    async def async_step_ev(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Vælg hvilken elbil der skal rettes, tilføj en ny, eller fjern en."""
        cars = self._cars()
        if not cars:
            self._car_id = NEW_CAR
            return await self.async_step_ev_car()
        if user_input is not None:
            choice = user_input[CAR_CHOICE]
            if user_input.get(CAR_REMOVE) and choice != NEW_CAR:
                return self._save_cars([c for c in cars if c["id"] != choice])
            self._car_id = choice
            return await self.async_step_ev_car()
        options = [{"value": c["id"], "label": c.get("name") or "Elbil"} for c in cars]
        options.append({"value": NEW_CAR, "label": "+ Tilføj ny elbil"})
        schema = vol.Schema({
            vol.Required(CAR_CHOICE, default=cars[0]["id"]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Optional(CAR_REMOVE, default=False): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="ev", data_schema=schema)

    async def async_step_ev_car(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Navn, lader, SoC og energimåler for én elbil."""
        cars = self._cars()
        current = next((c for c in cars if c["id"] == self._car_id), None)
        if user_input is not None:
            name = (user_input.get(CAR_NAME) or "Elbil").strip()
            data = {
                "name": name,
                "charger": user_input[CONF_EV_SWITCH],
                "soc": user_input.get(CONF_EV_SOC) or None,
                "energy": user_input.get(CONF_EV_ENERGY) or None,
                "current_entity": user_input.get(CAR_CURRENT_ENTITY) or None,
                "current_script": user_input.get(CAR_CURRENT_SCRIPT) or None,
                "easee_device": user_input.get(CAR_EASEE_DEVICE) or None,
                "plug": user_input.get(CAR_PLUG) or None,
                "power": user_input.get(CAR_POWER) or None,
            }
            if current:
                current.update(data)
            else:
                cars.append({"id": _new_car_id(name, cars), **data})
            return self._save_cars(cars)
        cur = current or {}
        default_name = cur.get("name") or ("Elbil" if not cars else f"Elbil {len(cars) + 1}")
        schema = vol.Schema({
            vol.Required(CAR_NAME, default=default_name): TEXT,
            vol.Required(CONF_EV_SWITCH, **({"default": cur["charger"]} if cur.get("charger") else {})): ONOFF,
            _opt(CONF_EV_SOC, {CONF_EV_SOC: cur.get("soc")}, SOC)[0]: SOC,
            _opt(CAR_PLUG, {CAR_PLUG: cur.get("plug")}, PLUG)[0]: PLUG,
            _opt(CAR_POWER, {CAR_POWER: cur.get("power")}, POWER)[0]: POWER,
            _opt(CONF_EV_ENERGY, {CONF_EV_ENERGY: cur.get("energy")}, ENERGY)[0]: ENERGY,
            _opt(CAR_EASEE_DEVICE, {CAR_EASEE_DEVICE: cur.get("easee_device")}, EASEE_DEVICE)[0]: EASEE_DEVICE,
            _opt(CAR_CURRENT_ENTITY, {CAR_CURRENT_ENTITY: cur.get("current_entity")}, CURRENT)[0]: CURRENT,
            _opt(CAR_CURRENT_SCRIPT, {CAR_CURRENT_SCRIPT: cur.get("current_script")}, SCRIPT)[0]: SCRIPT,
        })
        return self.async_show_form(step_id="ev_car", data_schema=schema,
                                    description_placeholders={"car": default_name})

    async def async_step_hotwater(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(HOTWATER_KEYS, user_input)
        fields = [(CONF_HOTWATER_ENTITY, HOTWATER), (CONF_HOTWATER_TEMP, TEMP), (CONF_HOTWATER_ENERGY, ENERGY)]
        return self.async_show_form(step_id="hotwater", data_schema=_entity_schema(fields, self._current()))

    async def async_step_heating(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(HEATING_KEYS, user_input)
        fields = [(CONF_HEATING_CLIMATE, CLIMATE), (CONF_HEATPUMP_ENERGY, ENERGY)]
        return self.async_show_form(step_id="heating", data_schema=_entity_schema(fields, self._current()))

"""Opsætning af SmartHus via brugerfladen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    AREAS,
    BATTERY_KEYS,
    CONF_AREA,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_BATTERY_MODE_ENTITY,
    CONF_BATTERY_OPT_CHARGE,
    CONF_BATTERY_OPT_DISCHARGE,
    CONF_BATTERY_OPT_NORMAL,
    CONF_BATTERY_SOC,
    CONF_ELAFGIFT,
    CONF_ENERGINET,
    CONF_EV_ENERGY,
    CONF_EV_SWITCH,
    CONF_EXPORT_ENERGY,
    CONF_HEATING_CLIMATE,
    CONF_HEATPUMP_ENERGY,
    CONF_HOTWATER_ENTITY,
    CONF_MARKUP,
    CONF_NAME,
    CONF_SALE_FEE,
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
    EV_KEYS,
    HEATING_KEYS,
    HOTWATER_KEYS,
    PRICE_KEYS,
    SOLAR_KEYS,
)

KR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=-5, max=10, step=0.001, mode=selector.NumberSelectorMode.BOX,
                                  unit_of_measurement="kr/kWh")
)
TEXT = selector.TextSelector()
ENERGY = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy"))
SOC = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"]))
MODE = selector.EntitySelector(selector.EntitySelectorConfig(domain=["select", "input_select"]))
ONOFF = selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "input_boolean"]))
HOTWATER = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["switch", "input_boolean", "water_heater", "climate"])
)
CLIMATE = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))


def _price_schema(values: dict) -> vol.Schema:
    v = {**DEFAULT_PRICES, **values}
    return vol.Schema(
        {
            vol.Required(CONF_AREA, default=v[CONF_AREA]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=AREAS, translation_key="price_area",
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


def _opt(key: str, current: dict, sel: Any, default: str | None = None) -> tuple:
    value = current.get(key, default)
    return vol.Optional(key, description={"suggested_value": value} if value else None), sel


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
        if user_input is not None:
            self._data.update(user_input)
            await self.async_set_unique_id(f"{self._data[CONF_NAME].lower()}_{user_input[CONF_AREA]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
        return self.async_show_form(step_id="prices", data_schema=_price_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SmartHusOptionsFlow()


class SmartHusOptionsFlow(OptionsFlow):
    """Tilføj/ret enheder og priser efter installation."""

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
        if user_input is not None:
            return self._save(PRICE_KEYS, user_input)
        return self.async_show_form(step_id="prices", data_schema=_price_schema(self._current()))

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(BATTERY_KEYS, user_input)
        fields = [
            (CONF_BATTERY_MODE_ENTITY, MODE),
            (CONF_BATTERY_OPT_CHARGE, TEXT),
            (CONF_BATTERY_OPT_DISCHARGE, TEXT),
            (CONF_BATTERY_OPT_NORMAL, TEXT),
            (CONF_BATTERY_SOC, SOC),
            (CONF_BATTERY_CHARGE_ENERGY, ENERGY),
            (CONF_BATTERY_DISCHARGE_ENERGY, ENERGY),
        ]
        return self.async_show_form(
            step_id="battery", data_schema=_entity_schema(fields, self._current(), DEFAULT_BATTERY_OPTIONS)
        )

    async def async_step_solar(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(SOLAR_KEYS, user_input)
        fields = [(CONF_SOLAR_ENERGY, ENERGY), (CONF_EXPORT_ENERGY, ENERGY), (CONF_SOLAR_CURTAIL, ONOFF)]
        return self.async_show_form(step_id="solar", data_schema=_entity_schema(fields, self._current()))

    async def async_step_ev(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(EV_KEYS, user_input)
        fields = [(CONF_EV_SWITCH, ONOFF), (CONF_EV_ENERGY, ENERGY)]
        return self.async_show_form(step_id="ev", data_schema=_entity_schema(fields, self._current()))

    async def async_step_hotwater(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(HOTWATER_KEYS, user_input)
        fields = [(CONF_HOTWATER_ENTITY, HOTWATER)]
        return self.async_show_form(step_id="hotwater", data_schema=_entity_schema(fields, self._current()))

    async def async_step_heating(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(HEATING_KEYS, user_input)
        fields = [(CONF_HEATING_CLIMATE, CLIMATE), (CONF_HEATPUMP_ENERGY, ENERGY)]
        return self.async_show_form(step_id="heating", data_schema=_entity_schema(fields, self._current()))

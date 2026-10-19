"""Justerbare indstillinger: antal billige timer, grader, temperaturer m.m."""

from __future__ import annotations

from dataclasses import dataclass, replace

from homeassistant.components.number import NumberEntityDescription, NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartHusConfigEntry
from .const import (
    CONF_BATTERY_MODE_ENTITY,
    CONF_HEATING_CLIMATE,
    CONF_HOTWATER_ENTITY,
    CONF_HOTWATER_TEMP,
    DEFAULT_SETTINGS,
    S_BATTERY_CHARGE_HOURS,
    S_BATTERY_DISCHARGE_HOURS,
    S_BATTERY_CAPACITY,
    S_BATTERY_MAX_SOC,
    S_BATTERY_MIN_SOC,
    S_BATTERY_MIN_SPREAD,
    S_EV_HOURS,
    S_EV_READY_HOUR,
    S_EV_TARGET_SOC,
    S_EV_CAPACITY,
    S_EV_CHARGE_POWER,
    S_EV_MAX_TOTAL_POWER,
    S_HEATING_BASE_TEMP,
    S_HEATING_BOOST_DEG,
    S_HEATING_BOOST_HOURS,
    S_HEATING_REDUCE_DEG,
    S_HEATING_REDUCE_HOURS,
    S_SOLAR_EXPORT_MIN,
    CONF_SOLAR_CURTAIL,
    S_HOTWATER_BOOST_TEMP,
    S_HOTWATER_HOURS,
    S_HOTWATER_NORMAL_TEMP,
    S_BATTERY_CHARGE_FROM,
    S_BATTERY_CHARGE_TO,
    S_BATTERY_DISCHARGE_FROM,
    S_BATTERY_DISCHARGE_TO,
    S_HOTWATER_FROM,
    S_HOTWATER_TO,
    S_HOTWATER_SURPLUS_KW,
    CONF_GRID_POWER,
    S_HEATING_BOOST_FROM,
    S_HEATING_BOOST_TO,
    S_HEATING_REDUCE_FROM,
    S_HEATING_REDUCE_TO,
)
from .controller import SmartHusController
from .entity import SmartHusEntity


@dataclass(frozen=True, kw_only=True)
class SmartHusNumberDescription(NumberEntityDescription):
    requires: str | None = None
    per_car: bool = False  # én pr. elbil; nøglen "ev_..." bliver til "<bil-id>_..."
    multi_car: bool = False  # kun når der er mindst to elbiler


H = UnitOfTime.HOURS
C = UnitOfTemperature.CELSIUS

NUMBERS: tuple[SmartHusNumberDescription, ...] = (
    SmartHusNumberDescription(key=S_BATTERY_CHARGE_HOURS, icon="mdi:battery-clock", native_min_value=0,
                              native_max_value=12, native_step=0.25, native_unit_of_measurement=H,
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_DISCHARGE_HOURS, icon="mdi:battery-clock-outline", native_min_value=0,
                              native_max_value=12, native_step=0.25, native_unit_of_measurement=H,
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_MIN_SPREAD, icon="mdi:cash-sync", native_min_value=0,
                              native_max_value=5, native_step=0.05, native_unit_of_measurement="kr/kWh",
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_MIN_SOC, icon="mdi:battery-10", native_min_value=0,
                              native_max_value=100, native_step=1, native_unit_of_measurement=PERCENTAGE,
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_MAX_SOC, icon="mdi:battery-90", native_min_value=0,
                              native_max_value=100, native_step=1, native_unit_of_measurement=PERCENTAGE,
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_CAPACITY, icon="mdi:battery-high", native_min_value=0.5,
                              native_max_value=200, native_step=0.5, native_unit_of_measurement="kWh",
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_EV_HOURS, icon="mdi:car-clock", native_min_value=0, native_max_value=24,
                              native_step=0.25, native_unit_of_measurement=H, per_car=True),
    SmartHusNumberDescription(key=S_EV_READY_HOUR, icon="mdi:clock-check", native_min_value=0,
                              native_max_value=23, native_step=1, native_unit_of_measurement="kl.",
                              per_car=True),
    SmartHusNumberDescription(key=S_EV_TARGET_SOC, icon="mdi:battery-charging-80", native_min_value=10,
                              native_max_value=100, native_step=5, native_unit_of_measurement=PERCENTAGE,
                              per_car=True),
    SmartHusNumberDescription(key=S_EV_CAPACITY, icon="mdi:car-battery", native_min_value=5,
                              native_max_value=200, native_step=1, native_unit_of_measurement="kWh",
                              per_car=True),
    SmartHusNumberDescription(key=S_EV_CHARGE_POWER, icon="mdi:ev-plug-type2", native_min_value=1,
                              native_max_value=50, native_step=0.1, native_unit_of_measurement="kW",
                              per_car=True),
    SmartHusNumberDescription(key=S_EV_MAX_TOTAL_POWER, icon="mdi:transmission-tower", native_min_value=0,
                              native_max_value=100, native_step=0.5, native_unit_of_measurement="kW",
                              multi_car=True),
    SmartHusNumberDescription(key=S_HOTWATER_HOURS, icon="mdi:water-thermometer", native_min_value=0,
                              native_max_value=12, native_step=0.25, native_unit_of_measurement=H,
                              requires=CONF_HOTWATER_ENTITY),
    SmartHusNumberDescription(key=S_HOTWATER_BOOST_TEMP, icon="mdi:thermometer-high", native_min_value=30,
                              native_max_value=80, native_step=0.5, native_unit_of_measurement=C,
                              requires=CONF_HOTWATER_ENTITY),
    SmartHusNumberDescription(key=S_HOTWATER_NORMAL_TEMP, icon="mdi:thermometer", native_min_value=20,
                              native_max_value=70, native_step=0.5, native_unit_of_measurement=C,
                              requires=CONF_HOTWATER_ENTITY),
    SmartHusNumberDescription(key=S_HOTWATER_SURPLUS_KW, icon="mdi:solar-power-variant", native_min_value=0,
                              native_max_value=20, native_step=0.1, native_unit_of_measurement="kW",
                              requires=CONF_GRID_POWER),
    SmartHusNumberDescription(key=S_HEATING_BASE_TEMP, icon="mdi:home-thermometer", native_min_value=10,
                              native_max_value=30, native_step=0.5, native_unit_of_measurement=C,
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_BOOST_DEG, icon="mdi:thermometer-plus", native_min_value=0,
                              native_max_value=5, native_step=0.5, native_unit_of_measurement=C,
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_BOOST_HOURS, icon="mdi:clock-plus", native_min_value=0,
                              native_max_value=12, native_step=0.25, native_unit_of_measurement=H,
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_REDUCE_DEG, icon="mdi:thermometer-minus", native_min_value=0,
                              native_max_value=5, native_step=0.5, native_unit_of_measurement=C,
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_BATTERY_CHARGE_FROM, icon="mdi:battery-clock", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_CHARGE_TO, icon="mdi:battery-clock", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_DISCHARGE_FROM, icon="mdi:battery-clock-outline", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_BATTERY_DISCHARGE_TO, icon="mdi:battery-clock-outline", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_BATTERY_MODE_ENTITY),
    SmartHusNumberDescription(key=S_HOTWATER_FROM, icon="mdi:water-clock", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_HOTWATER_ENTITY),
    SmartHusNumberDescription(key=S_HOTWATER_TO, icon="mdi:water-clock", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_HOTWATER_ENTITY),
    SmartHusNumberDescription(key=S_HEATING_BOOST_FROM, icon="mdi:clock-plus-outline", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_BOOST_TO, icon="mdi:clock-plus-outline", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_REDUCE_FROM, icon="mdi:clock-minus-outline", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_REDUCE_TO, icon="mdi:clock-minus-outline", native_min_value=0,
                              native_max_value=24, native_step=1, native_unit_of_measurement="kl.",
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_HEATING_REDUCE_HOURS, icon="mdi:clock-minus", native_min_value=0,
                              native_max_value=12, native_step=0.25, native_unit_of_measurement=H,
                              requires=CONF_HEATING_CLIMATE),
    SmartHusNumberDescription(key=S_SOLAR_EXPORT_MIN, icon="mdi:transmission-tower-off", native_min_value=-2,
                              native_max_value=5, native_step=0.01, native_unit_of_measurement="kr/kWh",
                              requires=CONF_SOLAR_CURTAIL),
)


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    entities: list[SmartHusNumber] = []
    for d in NUMBERS:
        if d.per_car:
            for car in controller.cars:
                cid = car["id"]
                desc = replace(d, key=f"{cid}_{d.key[3:]}", translation_key=d.key)
                entities.append(SmartHusNumber(entry, controller, desc, car.get("name") or "Elbil", d.key))
        elif d.multi_car:
            if len(controller.cars) >= 2:
                entities.append(SmartHusNumber(entry, controller, d))
        elif d.key == S_HOTWATER_SURPLUS_KW:
            if controller.has(CONF_HOTWATER_ENTITY) and controller.has(CONF_GRID_POWER):
                entities.append(SmartHusNumber(entry, controller, d))
        elif d.key in (S_HOTWATER_BOOST_TEMP, S_HOTWATER_NORMAL_TEMP):
            hw = controller.config.get(CONF_HOTWATER_ENTITY) or ""
            thermostat = hw.split(".")[0] in ("water_heater", "climate")
            if thermostat or (d.key == S_HOTWATER_BOOST_TEMP and hw and controller.has(CONF_HOTWATER_TEMP)):
                entities.append(SmartHusNumber(entry, controller, d))
        elif d.requires is None or controller.has(d.requires):
            entities.append(SmartHusNumber(entry, controller, d))
    async_add_entities(entities)


class SmartHusNumber(SmartHusEntity, RestoreNumber):
    _domain = "number"
    entity_description: SmartHusNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, controller: SmartHusController, description: SmartHusNumberDescription,
                 car_name: str | None = None, template_key: str | None = None) -> None:
        super().__init__(entry, controller, description.key,
                         description.translation_key if car_name else None, car_name)
        self.entity_description = description
        self._default = DEFAULT_SETTINGS.get(description.key, DEFAULT_SETTINGS.get(template_key or "", 0.0))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.controller.settings[self.entity_description.key] = float(last.native_value)

    @property
    def native_value(self) -> float:
        key = self.entity_description.key
        return self.controller.settings.get(key, self._default)

    async def async_set_native_value(self, value: float) -> None:
        await self.controller.async_set_setting(self.entity_description.key, float(value))
        self.async_write_ha_state()

"""Kontakter: slå styringen til/fra pr. modul og 'Lad elbil nu'."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SmartHusConfigEntry
from .const import (
    CONF_BATTERY_MODE_ENTITY,
    CONF_HEATING_CLIMATE,
    CONF_HOTWATER_ENTITY,
    CONF_GRID_POWER,
    F_HOTWATER_SURPLUS_SMART,
    F_HEATING_AUTO,
    F_HEATING_OFF_REDUCE,
    CONF_HEATING_CLIMATE,
    CONF_SOLAR_CURTAIL,
    MODULE_BATTERY,
    MODULE_HEATING,
    MODULE_HOTWATER,
    MODULE_MASTER,
    MODULE_SOLAR,
)
from .controller import SmartHusController
from .entity import SmartHusEntity

MODULE_SWITCHES = {
    MODULE_MASTER: ("control_master", "mdi:home-automation", None),
    MODULE_BATTERY: ("control_battery", "mdi:home-battery", CONF_BATTERY_MODE_ENTITY),
    MODULE_SOLAR: ("control_solar", "mdi:solar-power", CONF_SOLAR_CURTAIL),
    MODULE_HOTWATER: ("control_hotwater", "mdi:water-boiler", CONF_HOTWATER_ENTITY),
    MODULE_HEATING: ("control_heating", "mdi:heat-pump", CONF_HEATING_CLIMATE),
}


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    entities: list[SwitchEntity] = []
    for module, (key, icon, needs) in MODULE_SWITCHES.items():
        if needs is None or controller.has(needs):
            entities.append(ModuleSwitch(entry, controller, module, key, icon))
    for car in controller.cars:
        cid, name = car["id"], car.get("name") or "Elbil"
        entities.append(ModuleSwitch(entry, controller, cid, f"control_{cid}", "mdi:car-electric",
                                     translation_key="control_ev", car_name=name))
        entities.append(EvForceSwitch(entry, controller, cid, name))
        if not controller.has_soc_sensor(car):
            entities.append(FlagSwitch(entry, controller, f"{cid}_car_target", "mdi:car-cog",
                                       translation_key="ev_car_target", car_name=name, default=False))
        if controller.has_current_control(car) and controller.has(CONF_GRID_POWER):
            entities.append(FlagSwitch(entry, controller, f"{cid}_solar", "mdi:solar-power-variant",
                                       translation_key="ev_solar", car_name=name, default=False))
    if controller.has(CONF_HOTWATER_ENTITY) and controller.has(CONF_GRID_POWER):
        entities.append(FlagSwitch(entry, controller, F_HOTWATER_SURPLUS_SMART, "mdi:cash-sync", default=True))
    if controller.has(CONF_HEATING_CLIMATE):
        entities.append(FlagSwitch(entry, controller, F_HEATING_AUTO, "mdi:auto-mode", default=False))
    if controller.heating_air_to_air and controller.config.get(CONF_HEATING_CLIMATE):
        # Kun for små luft-til-luft varmepumper (fx vinterbeskyttelse): sluk helt i sparetimer
        entities.append(FlagSwitch(entry, controller, F_HEATING_OFF_REDUCE, "mdi:power-sleep", default=False))
    async_add_entities(entities)


class ModuleSwitch(SmartHusEntity, SwitchEntity, RestoreEntity):
    """Slår SmartHus-styring til/fra for et modul."""

    _domain = "switch"

    def __init__(self, entry, controller: SmartHusController, module: str, key: str, icon: str,
                 translation_key: str | None = None, car_name: str | None = None) -> None:
        super().__init__(entry, controller, key, translation_key, car_name)
        self.module = module
        self._attr_icon = icon
        if module != MODULE_MASTER:
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            # Gendan uden at sende kommandoer under opstart
            self.controller.enabled[self.module] = last.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.controller.enabled[self.module]

    async def async_turn_on(self, **kwargs) -> None:
        await self.controller.async_set_enabled(self.module, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.controller.async_set_enabled(self.module, False)
        self.async_write_ha_state()


class EvForceSwitch(SmartHusEntity, SwitchEntity, RestoreEntity):
    """Lad elbilen med det samme, uanset pris."""

    _domain = "switch"

    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, entry, controller: SmartHusController, cid: str, name: str) -> None:
        # Første bil beholder id'et switch.smarthus_ev_force
        super().__init__(entry, controller, "ev_force" if cid == "ev" else f"{cid}_force", "ev_force", name)
        self.cid = cid

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self.controller.ev_force[self.cid] = last.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.controller.ev_force.get(self.cid, False)

    async def async_turn_on(self, **kwargs) -> None:
        await self.controller.async_set_ev_force(self.cid, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.controller.async_set_ev_force(self.cid, False)
        self.async_write_ha_state()


class FlagSwitch(SmartHusEntity, SwitchEntity, RestoreEntity):
    """En indstilling der slås til/fra på kortet (fx solcelleladning eller 'kun når det betaler sig')."""

    _domain = "switch"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, controller: SmartHusController, key: str, icon: str,
                 translation_key: str | None = None, car_name: str | None = None, default: bool = False) -> None:
        super().__init__(entry, controller, key, translation_key, car_name)
        self.flag = key
        self._attr_icon = icon
        controller.flags.setdefault(key, default)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self.controller.flags[self.flag] = last.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.controller.flags.get(self.flag, False)

    async def async_turn_on(self, **kwargs) -> None:
        self.controller.flags[self.flag] = True
        self.controller.recalculate()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.controller.flags[self.flag] = False
        self.controller.recalculate()
        await self.controller.async_apply(force=True)
        self.async_write_ha_state()

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

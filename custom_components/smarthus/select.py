"""Valg af tilsluttet bil, når flere biler deler én lader."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SmartHusConfigEntry
from .controller import SmartHusController
from .entity import SmartHusEntity


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    entities = []
    for n, (charger, ids) in enumerate(controller.shared_chargers.items(), start=1):
        key = "connected_car" if n == 1 else f"connected_car_{n}"
        entities.append(ConnectedCarSelect(entry, controller, key, charger, ids))
    async_add_entities(entities)


class ConnectedCarSelect(SmartHusEntity, SelectEntity, RestoreEntity):
    """Hvilken bil sidder i laderen? Bruges når bilen ikke selv kan melde det."""

    _domain = "select"
    _attr_icon = "mdi:car-connected"

    def __init__(self, entry, controller: SmartHusController, key: str, charger: str, ids: list[str]) -> None:
        super().__init__(entry, controller, key, "connected_car")
        self.charger = charger
        self.ids = ids
        names: list[str] = []
        for cid in ids:
            name = (controller.car(cid) or {}).get("name") or "Elbil"
            names.append(name if name not in names else f"{name} ({cid})")
        self._by_name = dict(zip(names, ids))
        self._attr_options = names

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._by_name:
            self.controller.connected_choice[self.charger] = self._by_name[last.state]

    @property
    def current_option(self) -> str | None:
        """Den bil SmartHus behandler som tilsluttet lige nu."""
        active = next((cid for cid in self.ids if self.controller.connected.get(cid)), None)
        cid = active or self.controller.connected_choice.get(self.charger) or self.ids[0]
        return next((n for n, c in self._by_name.items() if c == cid), None)

    @property
    def extra_state_attributes(self) -> dict:
        src = {cid: self.controller.connected_source.get(cid) for cid in self.ids}
        auto = any(v == "sensor" for v in src.values())
        return {
            "charger": self.charger,
            "car_ids": self.ids,
            "detected_automatically": auto,
            "none_connected": not any(self.controller.connected.get(cid) for cid in self.ids),
        }

    async def async_select_option(self, option: str) -> None:
        self.controller.connected_choice[self.charger] = self._by_name[option]
        self.controller.recalculate()
        await self.controller.async_apply(force=True)
        self.async_write_ha_state()

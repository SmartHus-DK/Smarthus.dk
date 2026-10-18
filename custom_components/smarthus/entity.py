"""Fælles basisklasse for SmartHus-entiteter."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_NAME, DOMAIN, MANUFACTURER, SIGNAL_TICK
from .controller import SmartHusController


class SmartHusEntity(Entity):
    """Opdateres hvert minut og når priserne ændres."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _domain: str = ""  # sættes af hver platform, giver stabile entity-id'er

    def __init__(self, entry: ConfigEntry, controller: SmartHusController, key: str,
                 translation_key: str | None = None, car_name: str | None = None) -> None:
        self.entry = entry
        self.controller = controller
        self._attr_translation_key = translation_key or key
        if translation_key:
            self._attr_translation_placeholders = {"car": car_name or "Elbil"}
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        controller.expected_unique_ids.add(self._attr_unique_id)
        if self._domain:
            # Stabile id'er uanset sprog, fx sensor.smarthus_current_price
            self.entity_id = f"{self._domain}.smarthus_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME, "SmartHus"),
            manufacturer=MANUFACTURER,
            model="SmartHus energistyring",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://smarthus.dk",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_TICK.format(entry_id=self.entry.entry_id), self._handle_tick
            )
        )

    @callback
    def _handle_tick(self) -> None:
        self.async_write_ha_state()

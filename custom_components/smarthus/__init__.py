"""SmartHus – intelligent styring af solceller, batteri, elbil og varmepumpe efter elprisen."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .controller import SmartHusController
from .coordinator import SmartHusPriceCoordinator


@dataclass
class SmartHusData:
    coordinator: SmartHusPriceCoordinator
    controller: SmartHusController


type SmartHusConfigEntry = ConfigEntry[SmartHusData]


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry) -> bool:
    config = {**entry.data, **entry.options}
    coordinator = SmartHusPriceCoordinator(hass, entry, config)
    await coordinator.async_config_entry_first_refresh()

    controller = SmartHusController(hass, entry, coordinator, config)
    entry.runtime_data = SmartHusData(coordinator, controller)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartHusConfigEntry) -> bool:
    await entry.runtime_data.controller.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SmartHusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

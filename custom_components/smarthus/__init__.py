"""SmartHus – intelligent styring af solceller, batteri, elbil og varmepumpe efter elprisen."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CARD_URL, CARD_VERSION, DOMAIN, PLATFORMS, STATIC_URL
from .controller import SmartHusController
from .coordinator import SmartHusPriceCoordinator


@dataclass
class SmartHusData:
    coordinator: SmartHusPriceCoordinator
    controller: SmartHusController


type SmartHusConfigEntry = ConfigEntry[SmartHusData]

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Gør SmartHus-kortet tilgængeligt i alle dashboards (type: custom:smarthus-card)."""
    www = Path(__file__).parent / "www"
    try:
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(www), False)])
        add_extra_js_url(hass, f"{CARD_URL}?v={CARD_VERSION}")
    except Exception as err:  # noqa: BLE001 – kortet må aldrig stoppe integrationen
        _LOGGER.warning("Kunne ikke registrere SmartHus-kortet: %s", err)

    async def _import_solar_history(call: ServiceCall) -> ServiceResponse:
        """Genberegn solcelleregnskabet ud fra historikken (alle SmartHus-opsætninger)."""
        results = {}
        for entry in hass.config_entries.async_entries(DOMAIN):
            data = getattr(entry, "runtime_data", None)
            if data is None:
                continue
            try:
                results[entry.title] = await data.controller.async_import_solar_history(call.data.get("start_date"))
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
        if not results:
            raise HomeAssistantError("SmartHus er ikke sat op")
        return {"result": results}

    hass.services.async_register(
        DOMAIN, "import_solar_history", _import_solar_history,
        schema=vol.Schema({vol.Optional("start_date"): cv.date}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry) -> bool:
    config = {**entry.data, **entry.options}
    coordinator = SmartHusPriceCoordinator(hass, entry, config)
    await coordinator.async_config_entry_first_refresh()

    controller = SmartHusController(hass, entry, coordinator, config)
    entry.runtime_data = SmartHusData(coordinator, controller)
    await controller.async_load_savings()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale_entities(hass, entry, controller.expected_unique_ids)
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry, expected: set[str]) -> None:
    """Fjern entiteter for enheder der ikke længere er sat op (fx en fjernet elbil)."""
    if not expected:
        return
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id not in expected:
            _LOGGER.debug("Fjerner forældet SmartHus-entitet %s", ent.entity_id)
            reg.async_remove(ent.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmartHusConfigEntry) -> bool:
    await entry.runtime_data.controller.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SmartHusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

"""Henter spotpriser fra Energi Data Service."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import API_URL, CONF_AREA, DEFAULT_PRICES, DOMAIN
from .planner import Slot
from .pricing import PriceModel

_LOGGER = logging.getLogger(__name__)


class SmartHusPriceCoordinator(DataUpdateCoordinator[list[Slot]]):
    """Opdaterer priser hver 30. minut (nye priser for i morgen kommer ca. kl. 13)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=30),
            config_entry=entry,
        )
        self.config = config
        self.area = config.get(CONF_AREA, DEFAULT_PRICES[CONF_AREA])
        self.model = PriceModel(config, dt_util.get_default_time_zone())
        self._session = async_get_clientsession(hass)

    async def _async_update_data(self) -> list[Slot]:
        today = dt_util.now().date()
        params = {
            "start": (today - timedelta(days=1)).isoformat(),
            "end": (today + timedelta(days=2)).isoformat(),
            "filter": json.dumps({"PriceArea": [self.area]}),
            "columns": "TimeUTC,DayAheadPriceDKK",
            "sort": "TimeUTC ASC",
            "limit": "1000",
        }
        try:
            async with asyncio.timeout(30):
                async with self._session.get(API_URL, params=params) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if self.data:
                _LOGGER.warning("Kunne ikke hente elpriser, bruger seneste data: %s", err)
                return self.data
            raise UpdateFailed(f"Kunne ikke hente elpriser: {err}") from err

        slots = self.model.build_slots(payload.get("records", []))
        if not slots:
            if self.data:
                return self.data
            raise UpdateFailed("Energi Data Service returnerede ingen priser")
        return slots

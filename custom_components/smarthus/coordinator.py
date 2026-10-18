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

from .const import (
    API_URL,
    CONF_AREA,
    CONF_PRICE_SOURCE,
    CONF_SL_PRICE,
    CONF_SL_TOMORROW,
    DEFAULT_PRICES,
    DEFAULT_SL_PRICE,
    DEFAULT_SL_TOMORROW,
    DOMAIN,
    SOURCE_STROMLIGNING,
)
from .planner import Slot
from .pricing import PriceModel, apply_external_prices, parse_price_list

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
        self.source = config.get(CONF_PRICE_SOURCE) or "eds"
        self.sl_entities = [config.get(CONF_SL_PRICE) or DEFAULT_SL_PRICE,
                            config.get(CONF_SL_TOMORROW) or DEFAULT_SL_TOMORROW]
        self.external_hits = 0
        self._raw: list[Slot] = []

    @property
    def uses_stromligning(self) -> bool:
        return self.source == SOURCE_STROMLIGNING

    def _with_external(self, slots: list[Slot]) -> list[Slot]:
        """Brug kundens rigtige pris fra Strømligning, hvor den findes."""
        if not self.uses_stromligning:
            return slots
        prices = []
        for entity_id in self.sl_entities:
            st = self.hass.states.get(entity_id)
            if st is not None:
                prices += parse_price_list(st.attributes.get("prices"))
        prices = sorted({t: p for t, p in prices}.items())
        out, self.external_hits = apply_external_prices(slots, prices)
        if not self.external_hits:
            _LOGGER.warning("Ingen priser fra Strømligning (%s) – bruger SmartHus' egen beregning",
                            ", ".join(self.sl_entities))
        return out

    def async_refresh_external(self) -> None:
        """Strømligning har opdateret – genberegn uden at hente spotpriser igen."""
        if self._raw:
            self.async_set_updated_data(self._with_external(self._raw))

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
        if slots:
            self._raw = slots
            slots = self._with_external(slots)
        if not slots:
            if self.data:
                return self.data
            raise UpdateFailed("Energi Data Service returnerede ingen priser")
        return slots

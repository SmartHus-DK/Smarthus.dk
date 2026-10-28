"""Henter spotpriser fra Energi Data Service."""

from __future__ import annotations

import asyncio
import time
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
    CONF_GRID_COMPANY,
    GRID_MANUAL,
    CONF_PRICE_SOURCE,
    CONF_SALE_FEE,
    CONF_SL_PRICE,
    CONF_SL_SPOT,
    CONF_SL_TOMORROW,
    CONF_SL_PRICE_VAT,
    CONF_SL_SPOT_VAT,
    CONF_VAT,
    DEFAULT_PRICES,
    DEFAULT_SL_PRICE,
    DEFAULT_SL_SPOT,
    DEFAULT_SL_TOMORROW,
    DOMAIN,
    SOURCE_STROMLIGNING,
)
from .planner import Slot
from . import tariffs as tf
from .pricing import PriceModel, missing_tomorrow, apply_external_prices, apply_external_spot, has_vat, parse_price_list, vat_factor

_LOGGER = logging.getLogger(__name__)

NORMAL_INTERVAL = timedelta(minutes=30)
WAIT_INTERVAL = timedelta(minutes=5)     # mens vi venter på morgendagens priser


class SmartHusPriceCoordinator(DataUpdateCoordinator[list[Slot]]):
    """Opdaterer priser hver 30. minut (nye priser for i morgen kommer ca. kl. 13)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=NORMAL_INTERVAL,
            config_entry=entry,
        )
        self.config = config
        self.area = config.get(CONF_AREA, DEFAULT_PRICES[CONF_AREA])
        self.model = PriceModel(config, dt_util.get_default_time_zone())
        self._session = async_get_clientsession(hass)
        self.source = config.get(CONF_PRICE_SOURCE) or "eds"
        self.sl_entities = [config.get(CONF_SL_PRICE) or DEFAULT_SL_PRICE,
                            config.get(CONF_SL_TOMORROW) or DEFAULT_SL_TOMORROW]
        self.sl_spot = config.get(CONF_SL_SPOT) or DEFAULT_SL_SPOT
        self.external_hits = 0
        self._raw: list[Slot] = []
        company = config.get(CONF_GRID_COMPANY) or GRID_MANUAL
        self.grid_company = tf.GRID_COMPANIES.get(company)          # (navn, GLN, kode) eller None
        self._tariffs_fetched: float | None = None
        self.tariff_error: str | None = None

    @property
    def uses_stromligning(self) -> bool:
        return self.source == SOURCE_STROMLIGNING

    def _with_external(self, slots: list[Slot]) -> list[Slot]:
        """Brug kundens rigtige pris fra Strømligning, hvor den findes."""
        if not self.uses_stromligning:
            return slots
        prices = []
        want_vat = bool(self.config.get(CONF_VAT, DEFAULT_PRICES[CONF_VAT]))
        price_vat = has_vat(self.config.get(CONF_SL_PRICE_VAT), self.sl_entities[0])
        f = vat_factor(price_vat, want_vat)   # priser uden moms får lagt moms på – og omvendt
        for entity_id in self.sl_entities:
            st = self.hass.states.get(entity_id)
            if st is not None:
                prices += [(t, p * f) for t, p in parse_price_list(st.attributes.get("prices"))]
        prices = sorted({t: p for t, p in prices}.items())
        out, self.external_hits = apply_external_prices(slots, prices)
        st = self.hass.states.get(self.sl_spot)
        if st is not None:
            spot = parse_price_list(st.attributes.get("prices"))
            # Salgsprisen er altid uden moms – spotprisen omregnes efter valget i opsætningen
            fee = float(self.config.get(CONF_SALE_FEE, DEFAULT_PRICES[CONF_SALE_FEE]))
            spot_vat = has_vat(self.config.get(CONF_SL_SPOT_VAT), self.sl_spot)
            out, _ = apply_external_spot(out, spot, spot_vat, fee)
        if not self.external_hits:
            _LOGGER.warning("Ingen priser fra Strømligning (%s) – bruger SmartHus' egen beregning",
                            ", ".join(self.sl_entities))
        return out

    def async_refresh_external(self) -> None:
        """Strømligning har opdateret – genberegn uden at hente spotpriser igen."""
        if self._raw:
            self.async_set_updated_data(self._with_external(self._raw))
            if missing_tomorrow(self._raw, dt_util.now()):
                self.hass.async_create_task(self.async_request_refresh())   # måske er de kommet nu

    async def _async_refresh_tariffs(self) -> None:
        """Hent netselskabets tarif, Energinets tariffer og elafgiften (højst hver 12. time)."""
        if not self.grid_company:
            return
        now = time.monotonic()
        if self._tariffs_fetched is not None and now - self._tariffs_fetched < 12 * 3600:
            return
        _name, gln, code = self.grid_company
        queries = [
            {"GLN_Number": [gln], "ChargeTypeCode": [code], "ChargeType": ["D03"]},
            {"GLN_Number": [tf.ENERGINET_GLN], "ChargeTypeCode": [*tf.ENERGINET_CODES, tf.ELAFGIFT_CODE]},
        ]
        found: list[dict[str, list]] = []
        try:
            for flt in queries:
                params = {"filter": json.dumps(flt), "sort": "ValidFrom DESC", "limit": "500"}
                async with asyncio.timeout(30):
                    async with self._session.get(tf.DATASET_URL, params=params) as resp:
                        resp.raise_for_status()
                        found.append(tf.parse_rows((await resp.json()).get("records", [])))
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            self.tariff_error = f"Kunne ikke hente tariffer: {err}"
            _LOGGER.warning("SmartHus: %s – bruger %s", self.tariff_error,
                            "de senest hentede" if self.model.tariffs else "de indtastede satser")
            self._tariffs_fetched = now - 11 * 3600      # prøv igen om en time
            return
        grid = found[0].get(code, [])
        if not grid:
            self.tariff_error = f"Ingen tarif fundet for {_name} ({code})"
            _LOGGER.warning("SmartHus: %s – bruger de indtastede satser", self.tariff_error)
        else:
            self.tariff_error = None
        self.model.tariffs = tf.Tariffs(self.model.tz, grid, found[1])
        self._tariffs_fetched = now

    async def _async_update_data(self) -> list[Slot]:
        await self._async_refresh_tariffs()
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
        # Kig efter morgendagens priser hvert 5. minut, til de er kommet – ellers hver halve time
        self.update_interval = WAIT_INTERVAL if missing_tomorrow(slots, dt_util.now()) else NORMAL_INTERVAL
        return slots

"""Valg af tilsluttet bil, når flere biler deler én lader."""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import SmartHusConfigEntry
from .controller import SmartHusController
from .entity import SmartHusEntity
from .plug import NONE

NONE_OPTION = "Ingen bil"


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    entities = []
    for n, (charger, ids) in enumerate(controller.shared_chargers.items(), start=1):
        key = "connected_car" if n == 1 else f"connected_car_{n}"
        entities.append(ConnectedCarSelect(entry, controller, key, charger, ids))
    for car in controller.cars:
        entities.append(ReadyDaySelect(entry, controller, car["id"], car.get("name") or "Elbil"))
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
        self._attr_options = [*names, NONE_OPTION]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._by_name:
            self.controller.connected_choice[self.charger] = self._by_name[last.state]
        elif last is not None and last.state == NONE_OPTION:
            self.controller.connected_choice[self.charger] = NONE

    @property
    def current_option(self) -> str | None:
        """Den bil SmartHus behandler som tilsluttet lige nu."""
        active = next((cid for cid in self.ids if self.controller.connected.get(cid)), None)
        return next((n for n, c in self._by_name.items() if c == active), NONE_OPTION)

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
        self.controller.connected_choice[self.charger] = self._by_name.get(option, NONE)
        self.controller.recalculate()
        await self.controller.async_apply(force=True)
        self.async_write_ha_state()


class ReadyDaySelect(SmartHusEntity, SelectEntity, RestoreEntity):
    """Hvornår skal bilen lade: til "klar kl." eller billigst i en periode uden klar-tidspunkt.

    next: frem til næste "klar kl." · today: resten af i dag · tomorrow: i morgen 00–24 ·
    both: nu til og med i morgen · horizon: billigste i sigte (alle kendte priser).
    Dagene gemmes som faste datoer, så perioden ikke flytter sig. Når den er slut, går valget
    selv tilbage til "next".
    """

    _domain = "select"
    _attr_icon = "mdi:calendar-clock"
    _attr_options = ["next", "today", "tomorrow", "both", "horizon"]

    def __init__(self, entry, controller: SmartHusController, cid: str, name: str) -> None:
        key = "ev_ready_day" if cid == "ev" else f"{cid}_ready_day"
        super().__init__(entry, controller, key, "ev_ready_day", name)
        self.cid = cid

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        mode = last.attributes.get("mode")
        raw = last.attributes.get("ready_date")
        day = None
        if raw:
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                day = None
        if mode == "horizon":
            self.controller.ev_ready_mode[self.cid] = ("horizon", None)
        elif day is not None:
            # Ældre version uden tilstand (fast klar-dag) → tilbage til klar kl.
            if mode in ("today", "tomorrow", "both"):
                self.controller.ev_ready_mode[self.cid] = (mode, day)

    @property
    def current_option(self) -> str:
        mode, d = self.controller.ready_mode(self.cid)
        if mode in ("next", "horizon") or d is None:
            return mode if mode in ("next", "horizon") else "next"
        # Efter midnat er "i morgen" og "begge dage" blevet til "i dag"
        return "today" if d == dt_util.now().date() else mode

    @property
    def extra_state_attributes(self) -> dict:
        mode, d = self.controller.ready_mode(self.cid)
        inp = self.controller._ready_input(self.cid)
        return {"mode": mode, "ready_date": d.isoformat() if d else None,
                "ready_at": self.controller.ready_at(self.cid).isoformat(),
                "earliest": inp["earliest"].isoformat() if inp.get("earliest") else None,
                "uses_ready_hour": mode == "next"}

    async def async_select_option(self, option: str) -> None:
        today = dt_util.now().date()
        day = {"today": today, "tomorrow": today + timedelta(days=1), "both": today + timedelta(days=1)}.get(option)
        # (perioden slutter ved midnat efter den valgte dag)
        self.controller.set_ready_mode(self.cid, option if option in self._attr_options else "next", day)
        await self.controller.async_apply(force=True)
        self.async_write_ha_state()

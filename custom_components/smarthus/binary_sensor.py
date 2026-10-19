"""Binære sensorer: hvad planen ønsker lige nu, med planlagte perioder som attributter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartHusConfigEntry
from .controller import SmartHusController
from .entity import SmartHusEntity
from .planner import EvPlan, Plan, Slot, merge_periods


@dataclass(frozen=True, kw_only=True)
class SmartHusBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Plan], bool]
    slots_fn: Callable[[Plan], list[Slot]] | None = None


BINARY: tuple[SmartHusBinaryDescription, ...] = (
    SmartHusBinaryDescription(key="cheap_now", icon="mdi:cash-check", is_on_fn=lambda p: p.cheap_now),
    SmartHusBinaryDescription(key="expensive_now", icon="mdi:cash-remove", is_on_fn=lambda p: p.expensive_now),
    SmartHusBinaryDescription(key="battery_charge_now", icon="mdi:battery-charging",
                              is_on_fn=lambda p: p.battery_action == "charge", slots_fn=lambda p: p.battery_charge),
    SmartHusBinaryDescription(key="battery_discharge_now", icon="mdi:battery-arrow-down",
                              is_on_fn=lambda p: p.battery_action == "discharge",
                              slots_fn=lambda p: p.battery_discharge),
    SmartHusBinaryDescription(key="solar_curtail_now", icon="mdi:solar-power-variant-outline",
                              is_on_fn=lambda p: p.solar_curtail),
    SmartHusBinaryDescription(key="hotwater_now", icon="mdi:water-boiler", is_on_fn=lambda p: p.hotwater_on,
                              slots_fn=lambda p: p.hotwater_slots),
    SmartHusBinaryDescription(key="heating_boost_now", icon="mdi:thermometer-plus",
                              is_on_fn=lambda p: p.heating_mode == "boost", slots_fn=lambda p: p.heating_boost),
    SmartHusBinaryDescription(key="heating_reduce_now", icon="mdi:thermometer-minus",
                              is_on_fn=lambda p: p.heating_mode == "reduce", slots_fn=lambda p: p.heating_reduce),
)


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    entities: list[BinarySensorEntity] = [SmartHusBinary(entry, controller, d) for d in BINARY]
    entities += [EvChargeBinary(entry, controller, car["id"], car.get("name") or "Elbil") for car in controller.cars]
    async_add_entities(entities)


class SmartHusBinary(SmartHusEntity, BinarySensorEntity):
    _domain = "binary_sensor"
    entity_description: SmartHusBinaryDescription
    _unrecorded_attributes = frozenset({"periods", "slots"})

    def __init__(self, entry, controller: SmartHusController, description: SmartHusBinaryDescription) -> None:
        super().__init__(entry, controller, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.controller.plan)

    @property
    def extra_state_attributes(self) -> dict | None:
        fn = self.entity_description.slots_fn
        if not fn:
            return None
        slots = fn(self.controller.plan)
        attrs = {
            "periods": merge_periods(slots, self.controller.tz),
            "slots": [s.start.isoformat() for s in sorted(slots, key=lambda s: s.start)],
        }
        if self.entity_description.key == "hotwater_now":
            c = self.controller
            attrs["water_temp"] = c.hotwater_temp
            attrs["max_temp"] = c.settings.get("hotwater_boost_temp")
            attrs["warm_enough"] = c.hotwater_full
        return attrs


class EvChargeBinary(SmartHusEntity, BinarySensorEntity):
    """Om elbilen skal lade nu. Første bil beholder id'et binary_sensor.smarthus_ev_charge_now."""

    _domain = "binary_sensor"
    _attr_icon = "mdi:ev-station"
    _unrecorded_attributes = frozenset({"periods", "slots"})

    def __init__(self, entry, controller: SmartHusController, cid: str, name: str) -> None:
        super().__init__(entry, controller, f"{cid}_charge_now", "ev_charge_now", name)
        self.cid = cid

    def _ep(self) -> EvPlan:
        return self.controller.plan.evs.get(self.cid) or EvPlan(self.cid)

    @property
    def is_on(self) -> bool:
        return self._ep().on

    @property
    def extra_state_attributes(self) -> dict:
        ep = self._ep()
        return {
            "car_id": self.cid,
            "car_name": ep.name,
            "hours_needed": ep.hours_needed,
            "target_reached": ep.target_reached,
            "periods": merge_periods(ep.slots, self.controller.tz),
            "slots": [s.start.isoformat() for s in sorted(ep.slots, key=lambda s: s.start)],
        }

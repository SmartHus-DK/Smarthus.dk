"""Sensorer: elpriser, status og besparelser."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SmartHusConfigEntry
from .const import ACTION_OFF, MODULE_BATTERY, MODULE_EV, MODULE_HEATING, MODULE_HOTWATER, MODULE_SOLAR
from .controller import SmartHusController
from .entity import SmartHusEntity
from .planner import Slot, cheapest, local_days

PRICE_UNIT = "kr/kWh"


@dataclass(frozen=True, kw_only=True)
class SmartHusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SmartHusController], Any]
    attrs_fn: Callable[[SmartHusController], dict] | None = None


def _today(c: SmartHusController) -> list[Slot]:
    key = dt_util.now().date().isoformat()
    return local_days(c.coordinator.data or [], c.tz).get(key, [])


def _tomorrow(c: SmartHusController) -> list[Slot]:
    days = local_days(c.coordinator.data or [], c.tz)
    today = dt_util.now().date().isoformat()
    later = sorted(k for k in days if k > today)
    return days[later[0]] if later else []


def _series(slots: list[Slot]) -> list[dict]:
    return [{"start": s.start.isoformat(), "price": s.price, "spot": s.spot, "sale": s.sale} for s in slots]


def _current(c: SmartHusController, attr: str) -> float | None:
    cur = c.plan.current
    return getattr(cur, attr) if cur else None


def _price_attrs(c: SmartHusController) -> dict:
    cur = c.plan.current
    today, tomorrow = _today(c), _tomorrow(c)
    attrs: dict[str, Any] = {
        "today": _series(today),
        "tomorrow": _series(tomorrow),
        "tomorrow_available": bool(tomorrow),
        "price_area": c.coordinator.area,
    }
    if cur:
        attrs["spot"] = cur.spot
        attrs["sale"] = cur.sale
        attrs["net_tariff"] = c.coordinator.model.net_tariff(cur.start)
    return attrs


def _stat(fn: Callable[[list[float]], float]) -> Callable[[SmartHusController], float | None]:
    def inner(c: SmartHusController) -> float | None:
        vals = [s.price for s in _today(c)]
        return round(fn(vals), 4) if vals else None
    return inner


def _cheapest_hour(c: SmartHusController) -> datetime | None:
    future = [s for s in _today(c) + _tomorrow(c) if s.end > dt_util.utcnow()]
    best = cheapest(future, 1.0, contiguous=True)
    return best[0].start if best else None


def _battery_status(c: SmartHusController) -> str:
    if not c.active(MODULE_BATTERY):
        return ACTION_OFF
    return c.plan.battery_action


def _status(c: SmartHusController) -> str:
    p, parts = c.plan, []
    if c.active(MODULE_BATTERY) and p.battery_action == "charge":
        parts.append("Lader batteri")
    if c.active(MODULE_BATTERY) and p.battery_action == "discharge":
        parts.append("Bruger batteri")
    if c.active(MODULE_SOLAR) and p.solar_curtail:
        parts.append("Begrænser eksport")
    if c.active(MODULE_EV) and p.ev_on:
        parts.append("Lader elbil")
    elif c.active(MODULE_EV) and p.ev_target_reached:
        parts.append("Elbil opladet")
    if c.active(MODULE_HOTWATER) and p.hotwater_on:
        parts.append("Opvarmer varmt vand")
    if c.active(MODULE_HEATING) and p.heating_mode == "boost":
        parts.append("Ekstra varme")
    if c.active(MODULE_HEATING) and p.heating_mode == "reduce":
        parts.append("Sparer på varmen")
    if not parts:
        return "Billig strøm – klar" if p.cheap_now else "Normal drift"
    return " · ".join(parts)


def _ev_saving(c: SmartHusController) -> float | None:
    p = c.plan
    if p.ev_cost_now is None or p.ev_cost_planned is None:
        return None
    return round(p.ev_cost_now - p.ev_cost_planned, 2)


def _ev_attrs(c: SmartHusController) -> dict:
    p = c.plan
    e = p.ev_energy or 0
    return {
        "cost_now": p.ev_cost_now,
        "cost_planned": p.ev_cost_planned,
        "energy_kwh": p.ev_energy,
        "price_now": round(p.ev_cost_now / e, 3) if e and p.ev_cost_now is not None else None,
        "price_planned": round(p.ev_cost_planned / e, 3) if e and p.ev_cost_planned is not None else None,
        "soc": p.ev_soc,
        "target_soc": c.settings.get("ev_target_soc"),
        "hours_needed": p.ev_hours_needed,
        "target_reached": p.ev_target_reached,
    }


def _savings(bucket: str, category: str | None = None):
    return lambda c: c.savings.value(bucket, category)


def _savings_attrs(bucket: str):
    return lambda c: {cat: c.savings.value(bucket, cat) for cat in c.savings.data[bucket]}


PRICE = dict(native_unit_of_measurement=PRICE_UNIT, state_class=SensorStateClass.MEASUREMENT,
             suggested_display_precision=2)
MONEY = dict(device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="DKK",
             suggested_display_precision=2)

SENSORS: tuple[SmartHusSensorDescription, ...] = (
    SmartHusSensorDescription(key="current_price", icon="mdi:cash-clock", value_fn=lambda c: _current(c, "price"),
                              attrs_fn=_price_attrs, **PRICE),
    SmartHusSensorDescription(key="spot_price", icon="mdi:chart-line", value_fn=lambda c: _current(c, "spot"), **PRICE),
    SmartHusSensorDescription(key="sale_price", icon="mdi:transmission-tower-export",
                              value_fn=lambda c: _current(c, "sale"), **PRICE),
    SmartHusSensorDescription(key="today_average", icon="mdi:approximately-equal",
                              value_fn=_stat(lambda v: sum(v) / len(v)), **PRICE),
    SmartHusSensorDescription(key="today_min", icon="mdi:arrow-down-bold", value_fn=_stat(min), **PRICE),
    SmartHusSensorDescription(key="today_max", icon="mdi:arrow-up-bold", value_fn=_stat(max), **PRICE),
    SmartHusSensorDescription(key="cheapest_hour", icon="mdi:clock-star-four-points",
                              device_class=SensorDeviceClass.TIMESTAMP, value_fn=_cheapest_hour),
    SmartHusSensorDescription(key="battery_status", icon="mdi:home-battery", device_class=SensorDeviceClass.ENUM,
                              options=["charge", "discharge", "normal", "off"], value_fn=_battery_status),
    SmartHusSensorDescription(key="ev_hours_needed", icon="mdi:timer-sand", native_unit_of_measurement="h",
                              suggested_display_precision=1, value_fn=lambda c: c.plan.ev_hours_needed),
    SmartHusSensorDescription(key="ev_smart_saving", icon="mdi:cash-plus", value_fn=_ev_saving,
                              attrs_fn=_ev_attrs, **MONEY),
    SmartHusSensorDescription(key="status", icon="mdi:home-lightning-bolt", value_fn=_status),
    SmartHusSensorDescription(key="savings_today", icon="mdi:piggy-bank", value_fn=_savings("today"),
                              attrs_fn=_savings_attrs("today"), **MONEY),
    SmartHusSensorDescription(key="savings_month", icon="mdi:piggy-bank", value_fn=_savings("month_values"),
                              attrs_fn=_savings_attrs("month_values"), **MONEY),
    SmartHusSensorDescription(key="savings_total", icon="mdi:piggy-bank-outline", value_fn=_savings("total"),
                              attrs_fn=_savings_attrs("total"), state_class=SensorStateClass.TOTAL, **MONEY),
    SmartHusSensorDescription(key="savings_month_battery", icon="mdi:home-battery-outline",
                              value_fn=_savings("month_values", "battery"), **MONEY),
    SmartHusSensorDescription(key="savings_month_solar", icon="mdi:solar-power-variant",
                              value_fn=_savings("month_values", "solar"), **MONEY),
    SmartHusSensorDescription(key="savings_month_ev", icon="mdi:car-electric",
                              value_fn=_savings("month_values", "ev"), **MONEY),
    SmartHusSensorDescription(key="savings_month_heatpump", icon="mdi:heat-pump",
                              value_fn=_savings("month_values", "heatpump"), **MONEY),
)


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    async_add_entities(SmartHusSensor(entry, controller, d) for d in SENSORS)


class SmartHusSensor(SmartHusEntity, SensorEntity):
    _domain = "sensor"
    entity_description: SmartHusSensorDescription
    _unrecorded_attributes = frozenset({"today", "tomorrow"})

    def __init__(self, entry, controller, description: SmartHusSensorDescription) -> None:
        super().__init__(entry, controller, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.controller)

    @property
    def extra_state_attributes(self) -> dict | None:
        fn = self.entity_description.attrs_fn
        return fn(self.controller) if fn else None

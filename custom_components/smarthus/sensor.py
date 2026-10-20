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
from .const import (
    ACTION_OFF,
    CONF_EXPORT_ENERGY,
    CONF_IMPORT_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_FORECAST,
    CONF_SOLAR_POWER,
    MODULE_BATTERY,
    MODULE_HEATING,
    MODULE_HOTWATER,
    MODULE_SOLAR,
)
from .savings import import_price
from .controller import SmartHusController
from .entity import SmartHusEntity
from .planner import EvPlan, Slot, car_settings, cheapest, local_days

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
        "price_source": "Strømligning" if c.coordinator.uses_stromligning and c.coordinator.external_hits else "SmartHus",
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
    if c.active(MODULE_BATTERY) and c.reserve_holds:
        parts.append("Gemmer batteri til huset")
    if c.active(MODULE_BATTERY) and p.battery_action == "sell_solar":
        parts.append("Sælger solstrøm – batteriet lades billigt senere")
    if c.active(MODULE_SOLAR) and p.solar_curtail:
        parts.append("Begrænser eksport")
    for cid, ep in p.evs.items():
        if not c.active(cid) or not ep.connected:
            continue
        st = c.ev_solar.get(cid)
        if st and st.active:
            parts.append(f"Lader {ep.name} med sol ({st.current} A)")
        elif ep.on:
            parts.append(f"Lader {ep.name}")
        elif ep.target_reached:
            parts.append(f"{ep.name} opladet")
    if c.active(MODULE_HOTWATER) and p.hotwater_on:
        parts.append("Varmt vand er varmt nok" if c.hotwater_full else "Opvarmer varmt vand")
    elif c.active(MODULE_HOTWATER) and c.hotwater_surplus:
        parts.append("Varmt vand er varmt nok" if c.hotwater_full else "Varmer vand med solcelleoverskud")
    heating = c.active(MODULE_HEATING) and c.heating_outdoor_ok
    if heating and p.heating_mode == "boost":
        parts.append("Ekstra varme")
    if heating and p.heating_mode == "reduce":
        parts.append("Sparer på varmen")
    if not parts:
        return "Billig strøm – klar" if p.cheap_now else "Normal drift"
    return " · ".join(parts)


def _ev(c: SmartHusController, cid: str) -> EvPlan:
    return c.plan.evs.get(cid) or EvPlan(cid)


def _ev_saving(c: SmartHusController, cid: str) -> float | None:
    ep = _ev(c, cid)
    if ep.cost_now is None or ep.cost_planned is None:
        return None
    return round(ep.cost_now - ep.cost_planned, 2)


def _ev_attrs(c: SmartHusController, cid: str) -> dict:
    ep = _ev(c, cid)
    e = ep.energy or 0
    return {
        "car_id": cid,
        "connected": c.connected.get(cid, True),
        "car_name": (c.car(cid) or {}).get("name") or ep.name,
        "cost_now": ep.cost_now,
        "cost_planned": ep.cost_planned,
        "energy_kwh": ep.energy,
        "price_now": round(ep.cost_now / e, 3) if e and ep.cost_now is not None else None,
        "price_planned": round(ep.cost_planned / e, 3) if e and ep.cost_planned is not None else None,
        "soc": ep.soc,
        "target_soc": car_settings(c.settings, cid)["target_soc"],
        "hours_needed": ep.hours_needed,
        "target_reached": ep.target_reached,
        "limited_by_power": ep.limited,
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
    SmartHusSensorDescription(key="energy_flow", icon="mdi:home-lightning-bolt-outline", native_unit_of_measurement="kW",
                              suggested_display_precision=2, value_fn=lambda c: c.flow.get("house_kw"),
                              attrs_fn=lambda c: c.flow),
    SmartHusSensorDescription(key="outdoor_temperature", icon="mdi:thermometer", device_class=SensorDeviceClass.TEMPERATURE,
                              native_unit_of_measurement="°C", suggested_display_precision=1,
                              value_fn=lambda c: c.outdoor_temp, attrs_fn=lambda c: c.weather_info),
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
                              options=["charge", "discharge", "normal", "sell_solar", "off"], value_fn=_battery_status),
    SmartHusSensorDescription(key="status", icon="mdi:home-lightning-bolt", value_fn=_status),
    SmartHusSensorDescription(key="savings_today", icon="mdi:piggy-bank", value_fn=_savings("today"),
                              attrs_fn=_savings_attrs("today"), **MONEY),
    SmartHusSensorDescription(key="savings_month", icon="mdi:piggy-bank", value_fn=_savings("month_values"),
                              attrs_fn=_savings_attrs("month_values"), **MONEY),
    SmartHusSensorDescription(key="savings_total", icon="mdi:piggy-bank-outline", value_fn=_savings("total"),
                              attrs_fn=_savings_attrs("total"), state_class=SensorStateClass.TOTAL, **MONEY),
    SmartHusSensorDescription(key="savings_month_battery", icon="mdi:home-battery-outline",
                              value_fn=_savings("month_values", "battery"), **MONEY),
    SmartHusSensorDescription(key="savings_month_ev", icon="mdi:car-electric",
                              value_fn=_savings("month_values", "ev"), **MONEY),
    SmartHusSensorDescription(key="savings_month_heatpump", icon="mdi:heat-pump",
                              value_fn=_savings("month_values", "heatpump"), **MONEY),
    SmartHusSensorDescription(key="savings_month_hotwater", icon="mdi:water-boiler",
                              value_fn=_savings("month_values", "hotwater"), **MONEY),
)

# Batteriets reserve ud fra husets lærte forbrugsmønster
def _reserve_attrs(c: SmartHusController) -> dict:
    attrs = c.reserve.as_dict() if c.reserve else {}
    p = c.profile
    today = dt_util.now()
    attrs.update({
        "learned_days": p.days,
        "profile_ready": p.ready,
        "profile_updated": c.profile_updated.isoformat() if c.profile_updated else None,
        "profile_today": [round(v, 2) for v in p.hours_for(today)],
        "holding_for_reserve": c.reserve_holds,
        "sell_solar": c.sell_solar,
    })
    return attrs


RESERVE_SENSORS: tuple[SmartHusSensorDescription, ...] = (
    SmartHusSensorDescription(
        key="battery_reserve", icon="mdi:battery-lock", device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement="kWh", suggested_display_precision=1,
        value_fn=lambda c: round(c.reserve.reserve_kwh, 2) if c.reserve else None, attrs_fn=_reserve_attrs),
    SmartHusSensorDescription(
        key="battery_sellable", icon="mdi:battery-arrow-up-outline", device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement="kWh", suggested_display_precision=1,
        value_fn=lambda c: round(c.reserve.sellable_kwh, 2) if c.reserve and c.reserve.sellable_kwh is not None else None),
)


# Solcelleoverblik: produktion, sparet (egetforbrug) og tjent (salg) pr. periode
_SOLAR_PERIODS = (("today", "today"), ("month", "month"), ("year", "year"), ("total", "total"))


def _solar_value(bucket: str):
    return lambda c: c.savings.solar(bucket) if c.has(CONF_SOLAR_ENERGY) else None


def _solar_field(bucket: str, field: str):
    return lambda c: c.savings.solar(bucket, field) if c.has(CONF_SOLAR_ENERGY) else None


def _solar_value_attrs(bucket: str):
    def fn(c: SmartHusController) -> dict:
        attrs = c.savings.solar_attrs(bucket)
        attrs["has_export_meter"] = c.has(CONF_EXPORT_ENERGY)
        attrs["has_import_meter"] = c.has(CONF_IMPORT_ENERGY)
        exp = attrs.get("export_kwh") or 0
        attrs["export_price"] = round(attrs.get("export_income", 0) / exp, 3) if exp > 0.05 else None
        if c.has(CONF_IMPORT_ENERGY):
            attrs["import_price"] = import_price(c.savings.data["solar"][bucket])
        if bucket == "total":
            attrs.update(c.savings.solar_history())
        return attrs
    return fn


SOLAR_SENSORS: tuple[SmartHusSensorDescription, ...] = tuple(
    d
    for bucket, name in _SOLAR_PERIODS
    for d in (
        SmartHusSensorDescription(
            key=f"solar_produced_{name}", icon="mdi:solar-power-variant", value_fn=_solar_field(bucket, "produced"),
            device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement="kWh", suggested_display_precision=1,
            state_class=SensorStateClass.TOTAL if bucket == "total" else None),
        SmartHusSensorDescription(
            key=f"solar_value_{name}", icon="mdi:cash-plus", value_fn=_solar_value(bucket),
            attrs_fn=_solar_value_attrs(bucket),
            state_class=SensorStateClass.TOTAL if bucket == "total" else None, **MONEY),
        SmartHusSensorDescription(
            key=f"solar_saved_{name}", icon="mdi:home-lightning-bolt-outline",
            value_fn=_solar_field(bucket, "self_value"), **MONEY),
        SmartHusSensorDescription(
            key=f"solar_sold_{name}", icon="mdi:transmission-tower-export",
            value_fn=_solar_field(bucket, "export_income"), **MONEY),
    )
)


# Strøm købt fra nettet pr. periode (kræver en importmåler)
GRID_SENSORS: tuple[SmartHusSensorDescription, ...] = tuple(
    d
    for bucket, name in _SOLAR_PERIODS
    for d in (
        SmartHusSensorDescription(
            key=f"grid_import_{name}", icon="mdi:transmission-tower-import",
            value_fn=lambda c, b=bucket: c.savings.solar(b, "import_kwh"),
            device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement="kWh", suggested_display_precision=1),
        SmartHusSensorDescription(
            key=f"grid_cost_{name}", icon="mdi:cash-minus",
            value_fn=lambda c, b=bucket: c.savings.solar(b, "import_cost"),
            attrs_fn=lambda c, b=bucket: {"avg_price": import_price(c.savings.data["solar"][b])}, **MONEY),
    )
)


FORECAST_SENSOR = SmartHusSensorDescription(
    key="solar_forecast", icon="mdi:solar-power-variant-outline", device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement="kWh", suggested_display_precision=1,
    value_fn=lambda c: c.solar_forecast.get("remaining_kwh"), attrs_fn=lambda c: c.solar_forecast)


async def async_setup_entry(hass: HomeAssistant, entry: SmartHusConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data.controller
    entities: list[SensorEntity] = [SmartHusSensor(entry, controller, d) for d in SENSORS]
    if controller.has(CONF_HOUSE_ENERGY):
        entities += [SmartHusSensor(entry, controller, d) for d in RESERVE_SENSORS]
    if controller.has(CONF_SOLAR_ENERGY):
        entities += [SmartHusSensor(entry, controller, d) for d in SOLAR_SENSORS]
    if controller.has(CONF_IMPORT_ENERGY):
        entities += [SmartHusSensor(entry, controller, d) for d in GRID_SENSORS]
    if controller.has(CONF_SOLAR_ENERGY) or controller.has(CONF_SOLAR_POWER) or controller.has(CONF_SOLAR_FORECAST):
        entities.append(SmartHusSensor(entry, controller, FORECAST_SENSOR))
    for car in controller.cars:
        for d in EV_SENSORS:
            entities.append(EvSensor(entry, controller, d, car["id"], car.get("name") or "Elbil"))
    async_add_entities(entities)


class SmartHusSensor(SmartHusEntity, SensorEntity):
    _domain = "sensor"
    entity_description: SmartHusSensorDescription
    _unrecorded_attributes = frozenset({"today", "tomorrow", "days", "months", "hours"})

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


@dataclass(frozen=True, kw_only=True)
class EvSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SmartHusController, str], Any]
    attrs_fn: Callable[[SmartHusController, str], dict] | None = None


EV_SENSORS: tuple[EvSensorDescription, ...] = (
    EvSensorDescription(key="ev_hours_needed", icon="mdi:timer-sand", native_unit_of_measurement="h",
                        suggested_display_precision=1, value_fn=lambda c, cid: _ev(c, cid).hours_needed),
    EvSensorDescription(key="ev_smart_saving", icon="mdi:cash-plus", value_fn=_ev_saving,
                        attrs_fn=_ev_attrs, **MONEY),
)


class EvSensor(SmartHusEntity, SensorEntity):
    """Sensor for én elbil. Første bil beholder id'erne sensor.smarthus_ev_*."""

    _domain = "sensor"
    entity_description: EvSensorDescription

    def __init__(self, entry, controller, description: EvSensorDescription, cid: str, name: str) -> None:
        key = description.key.replace("ev_", f"{cid}_", 1)
        super().__init__(entry, controller, key, description.key, name)
        self.entity_description = description
        self.cid = cid

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.controller, self.cid)

    @property
    def extra_state_attributes(self) -> dict | None:
        fn = self.entity_description.attrs_fn
        return fn(self.controller, self.cid) if fn else None

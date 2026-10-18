"""Konstanter for SmartHus-integrationen."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smarthus"
MANUFACTURER: Final = "SmartHus.dk"

API_URL: Final = "https://api.energidataservice.dk/dataset/DayAheadPrices"

CARD_URL: Final = "/smarthus_static/smarthus-card.js"
CARD_VERSION: Final = "1.3.0"

PLATFORMS: Final = ["sensor", "binary_sensor", "switch", "number"]

SIGNAL_TICK: Final = "smarthus_tick_{entry_id}"

# --- Grundopsætning (entry.data / options) ---------------------------------
CONF_NAME: Final = "name"
CONF_AREA: Final = "price_area"
CONF_VAT: Final = "include_vat"
CONF_MARKUP: Final = "supplier_markup"
CONF_ENERGINET: Final = "energinet_tariff"
CONF_ELAFGIFT: Final = "elafgift"
CONF_SALE_FEE: Final = "sale_fee"
CONF_T_WINTER_LOW: Final = "tariff_winter_low"
CONF_T_WINTER_HIGH: Final = "tariff_winter_high"
CONF_T_WINTER_PEAK: Final = "tariff_winter_peak"
CONF_T_SUMMER_LOW: Final = "tariff_summer_low"
CONF_T_SUMMER_HIGH: Final = "tariff_summer_high"
CONF_T_SUMMER_PEAK: Final = "tariff_summer_peak"

AREAS: Final = ["DK1", "DK2"]

# Standardværdier i kr/kWh ekskl. moms. Tjek altid dit eget netselskab.
DEFAULT_PRICES: Final = {
    CONF_AREA: "DK1",
    CONF_VAT: True,
    CONF_MARKUP: 0.05,
    CONF_ENERGINET: 0.14,
    CONF_ELAFGIFT: 0.008,
    CONF_SALE_FEE: 0.03,
    CONF_T_WINTER_LOW: 0.13,
    CONF_T_WINTER_HIGH: 0.39,
    CONF_T_WINTER_PEAK: 1.17,
    CONF_T_SUMMER_LOW: 0.13,
    CONF_T_SUMMER_HIGH: 0.20,
    CONF_T_SUMMER_PEAK: 0.52,
}

PRICE_KEYS: Final = list(DEFAULT_PRICES)

# --- Enheder (options) -----------------------------------------------------
# Batteri / inverter
CONF_BATTERY_MODE_ENTITY: Final = "battery_mode_entity"
CONF_BATTERY_OPT_CHARGE: Final = "battery_option_charge"
CONF_BATTERY_OPT_DISCHARGE: Final = "battery_option_discharge"
CONF_BATTERY_OPT_NORMAL: Final = "battery_option_normal"
CONF_BATTERY_SOC: Final = "battery_soc_sensor"
CONF_BATTERY_CHARGE_ENERGY: Final = "battery_charge_energy_sensor"
CONF_BATTERY_DISCHARGE_ENERGY: Final = "battery_discharge_energy_sensor"
# Solceller
CONF_SOLAR_ENERGY: Final = "solar_energy_sensor"
CONF_EXPORT_ENERGY: Final = "grid_export_energy_sensor"
CONF_SOLAR_CURTAIL: Final = "solar_curtail_entity"
# Elbil
CONF_EV_SWITCH: Final = "ev_charger_entity"
CONF_EV_ENERGY: Final = "ev_energy_sensor"
CONF_EV_SOC: Final = "ev_soc_sensor"
# Varmt vand
CONF_HOTWATER_ENTITY: Final = "hotwater_entity"
# Varme / varmepumpe
CONF_HEATING_CLIMATE: Final = "heating_climate_entity"
CONF_HEATPUMP_ENERGY: Final = "heatpump_energy_sensor"

BATTERY_KEYS: Final = [
    CONF_BATTERY_MODE_ENTITY,
    CONF_BATTERY_OPT_CHARGE,
    CONF_BATTERY_OPT_DISCHARGE,
    CONF_BATTERY_OPT_NORMAL,
    CONF_BATTERY_SOC,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
]
SOLAR_KEYS: Final = [CONF_SOLAR_ENERGY, CONF_EXPORT_ENERGY, CONF_SOLAR_CURTAIL]
EV_KEYS: Final = [CONF_EV_SWITCH, CONF_EV_ENERGY, CONF_EV_SOC]
HOTWATER_KEYS: Final = [CONF_HOTWATER_ENTITY]
HEATING_KEYS: Final = [CONF_HEATING_CLIMATE, CONF_HEATPUMP_ENERGY]

# Standardnavne på SigEnergy-tilstande (kan ændres i opsætningen)
DEFAULT_BATTERY_OPTIONS: Final = {
    CONF_BATTERY_OPT_CHARGE: "Command Charging (Grid First)",
    CONF_BATTERY_OPT_DISCHARGE: "Command Discharging (ESS First)",
    CONF_BATTERY_OPT_NORMAL: "Maximum Self Consumption",
}

# --- Moduler ---------------------------------------------------------------
MODULE_MASTER: Final = "master"
MODULE_BATTERY: Final = "battery"
MODULE_SOLAR: Final = "solar"
MODULE_EV: Final = "ev"
MODULE_HOTWATER: Final = "hotwater"
MODULE_HEATING: Final = "heating"
MODULES: Final = [MODULE_BATTERY, MODULE_SOLAR, MODULE_EV, MODULE_HOTWATER, MODULE_HEATING]

# --- Justerbare indstillinger (number-entiteter) ---------------------------
S_BATTERY_CHARGE_HOURS: Final = "battery_charge_hours"
S_BATTERY_DISCHARGE_HOURS: Final = "battery_discharge_hours"
S_BATTERY_MIN_SPREAD: Final = "battery_min_spread"
S_BATTERY_MIN_SOC: Final = "battery_min_soc"
S_BATTERY_MAX_SOC: Final = "battery_max_soc"
S_EV_HOURS: Final = "ev_hours"
S_EV_READY_HOUR: Final = "ev_ready_hour"
S_EV_TARGET_SOC: Final = "ev_target_soc"
S_EV_CAPACITY: Final = "ev_battery_capacity"
S_EV_CHARGE_POWER: Final = "ev_charge_power"
S_HOTWATER_HOURS: Final = "hotwater_hours"
S_HOTWATER_BOOST_TEMP: Final = "hotwater_boost_temp"
S_HOTWATER_NORMAL_TEMP: Final = "hotwater_normal_temp"
S_HEATING_BASE_TEMP: Final = "heating_base_temp"
S_HEATING_BOOST_DEG: Final = "heating_boost_degrees"
S_HEATING_BOOST_HOURS: Final = "heating_boost_hours"
S_HEATING_REDUCE_DEG: Final = "heating_reduce_degrees"
S_HEATING_REDUCE_HOURS: Final = "heating_reduce_hours"

S_BATTERY_CHARGE_FROM: Final = "battery_charge_from"
S_BATTERY_CHARGE_TO: Final = "battery_charge_to"
S_BATTERY_DISCHARGE_FROM: Final = "battery_discharge_from"
S_BATTERY_DISCHARGE_TO: Final = "battery_discharge_to"
S_HOTWATER_FROM: Final = "hotwater_from"
S_HOTWATER_TO: Final = "hotwater_to"
S_HEATING_BOOST_FROM: Final = "heating_boost_from"
S_HEATING_BOOST_TO: Final = "heating_boost_to"
S_HEATING_REDUCE_FROM: Final = "heating_reduce_from"
S_HEATING_REDUCE_TO: Final = "heating_reduce_to"

DEFAULT_SETTINGS: Final = {
    S_BATTERY_CHARGE_HOURS: 3.0,
    S_BATTERY_DISCHARGE_HOURS: 3.0,
    S_BATTERY_MIN_SPREAD: 0.5,
    S_BATTERY_MIN_SOC: 15.0,
    S_BATTERY_MAX_SOC: 95.0,
    S_EV_HOURS: 4.0,
    S_EV_READY_HOUR: 7.0,
    S_EV_TARGET_SOC: 80.0,
    S_EV_CAPACITY: 60.0,
    S_EV_CHARGE_POWER: 11.0,
    S_HOTWATER_HOURS: 2.0,
    S_HOTWATER_BOOST_TEMP: 55.0,
    S_HOTWATER_NORMAL_TEMP: 45.0,
    S_HEATING_BASE_TEMP: 21.0,
    S_HEATING_BOOST_DEG: 1.0,
    S_HEATING_BOOST_HOURS: 4.0,
    S_HEATING_REDUCE_DEG: 1.0,
    S_HEATING_REDUCE_HOURS: 3.0,
    S_BATTERY_CHARGE_FROM: 0.0,
    S_BATTERY_CHARGE_TO: 24.0,
    S_BATTERY_DISCHARGE_FROM: 0.0,
    S_BATTERY_DISCHARGE_TO: 24.0,
    S_HOTWATER_FROM: 0.0,
    S_HOTWATER_TO: 24.0,
    S_HEATING_BOOST_FROM: 0.0,
    S_HEATING_BOOST_TO: 24.0,
    S_HEATING_REDUCE_FROM: 0.0,
    S_HEATING_REDUCE_TO: 24.0,
}

# --- Handlinger ------------------------------------------------------------
ACTION_CHARGE: Final = "charge"
ACTION_DISCHARGE: Final = "discharge"
ACTION_NORMAL: Final = "normal"
ACTION_OFF: Final = "off"
HEATING_BOOST: Final = "boost"
HEATING_REDUCE: Final = "reduce"

SAVINGS_CATEGORIES: Final = [MODULE_BATTERY, MODULE_SOLAR, MODULE_EV, "heatpump"]

# Minimum tid mellem to kald til samme enhed (sekunder)
MIN_COMMAND_INTERVAL: Final = 90

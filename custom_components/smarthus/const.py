"""Konstanter for SmartHus-integrationen."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smarthus"
MANUFACTURER: Final = "SmartHus.dk"

API_URL: Final = "https://api.energidataservice.dk/dataset/DayAheadPrices"

STATIC_URL: Final = "/smarthus_static"
CARD_URL: Final = f"{STATIC_URL}/smarthus-card.js"
CARD_VERSION: Final = "1.15.2"

PLATFORMS: Final = ["sensor", "binary_sensor", "switch", "number", "select"]

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

# Priskilde: egne tariffer (Energi Data Service) eller Strømligning-integrationen
CONF_PRICE_SOURCE: Final = "price_source"
SOURCE_EDS: Final = "eds"
SOURCE_STROMLIGNING: Final = "stromligning"
CONF_SL_PRICE: Final = "stromligning_price_entity"
CONF_SL_TOMORROW: Final = "stromligning_tomorrow_entity"
DEFAULT_SL_PRICE: Final = "sensor.stromligning_current_price_vat"
DEFAULT_SL_TOMORROW: Final = "binary_sensor.stromligning_tomorrow_available_vat"
CONF_SL_SPOT: Final = "stromligning_spot_entity"
DEFAULT_SL_SPOT: Final = "sensor.stromligning_spotprice_vat"

PRICE_KEYS: Final = list(DEFAULT_PRICES) + [CONF_PRICE_SOURCE, CONF_SL_PRICE, CONF_SL_TOMORROW, CONF_SL_SPOT]

# --- Enheder (options) -----------------------------------------------------
# Batteri / inverter
CONF_BATTERY_MODE_ENTITY: Final = "battery_mode_entity"
CONF_BATTERY_OPT_CHARGE: Final = "battery_option_charge"
CONF_BATTERY_OPT_DISCHARGE: Final = "battery_option_discharge"
CONF_BATTERY_OPT_NORMAL: Final = "battery_option_normal"
CONF_BATTERY_SOC: Final = "battery_soc_sensor"
CONF_BATTERY_CHARGE_ENERGY: Final = "battery_charge_energy_sensor"
CONF_BATTERY_DISCHARGE_ENERGY: Final = "battery_discharge_energy_sensor"
# Husets forbrug (valgfri) – bruges til at lære forbrugsmønsteret og holde en reserve i batteriet
CONF_HOUSE_ENERGY: Final = "house_energy_sensor"
# Scripts til batteriet (alternativ til en rullemenu – virker med alle invertere)
CONF_BATTERY_SCRIPT_CHARGE: Final = "battery_script_charge"
CONF_BATTERY_SCRIPT_DISCHARGE: Final = "battery_script_discharge"
CONF_BATTERY_SCRIPT_NORMAL: Final = "battery_script_normal"
BATTERY_SCRIPTS: Final = [CONF_BATTERY_SCRIPT_CHARGE, CONF_BATTERY_SCRIPT_DISCHARGE, CONF_BATTERY_SCRIPT_NORMAL]
# Indbyggede profiler (ingen scripts nødvendige)
CONF_HUAWEI_DEVICE: Final = "battery_huawei_device"
CONF_HUAWEI_POWER: Final = "battery_huawei_power"
DEFAULT_HUAWEI_POWER: Final = 5000
# Batteriets effekt lige nu (til energiflowet)
CONF_BATTERY_POWER: Final = "battery_power_sensor"
CONF_BATTERY_POWER_SIGN: Final = "battery_power_sign"
SIGN_DISCHARGE_POSITIVE: Final = "discharge_positive"
SIGN_CHARGE_POSITIVE: Final = "charge_positive"
# Solceller
CONF_SOLAR_ENERGY: Final = "solar_energy_sensor"
CONF_EXPORT_ENERGY: Final = "grid_export_energy_sensor"
CONF_SOLAR_CURTAIL: Final = "solar_curtail_entity"
# Eksportstyring: kontakt, rullemenu, tal (eksportgrænse) eller scripts
CONF_EXPORT_OPT_STOP: Final = "export_option_stop"
CONF_EXPORT_OPT_NORMAL: Final = "export_option_normal"
CONF_EXPORT_VALUE_STOP: Final = "export_value_stop"
CONF_EXPORT_VALUE_NORMAL: Final = "export_value_normal"
CONF_EXPORT_SCRIPT_STOP: Final = "export_script_stop"
CONF_EXPORT_SCRIPT_START: Final = "export_script_start"
EXPORT_SCRIPTS: Final = [CONF_EXPORT_SCRIPT_STOP, CONF_EXPORT_SCRIPT_START]
# Solprognose (valgfri): sensor med forventet resterende produktion i dag (kWh)
CONF_SOLAR_FORECAST: Final = "solar_forecast_sensor"
# Elbil
CONF_EV_SWITCH: Final = "ev_charger_entity"
CONF_EV_ENERGY: Final = "ev_energy_sensor"
CONF_EV_SOC: Final = "ev_soc_sensor"
# Varmt vand
CONF_HOTWATER_ENTITY: Final = "hotwater_entity"
CONF_HOTWATER_ENERGY: Final = "hotwater_energy_sensor"
CONF_HOTWATER_TEMP: Final = "hotwater_temp_sensor"
# Effekt til/fra nettet lige nu (bruges til solcelleoverskud)
CONF_GRID_POWER: Final = "grid_power_sensor"
CONF_GRID_POWER_SIGN: Final = "grid_power_sign"
SIGN_EXPORT_POSITIVE: Final = "export_positive"
SIGN_IMPORT_POSITIVE: Final = "import_positive"
S_HOTWATER_SURPLUS_KW: Final = "hotwater_surplus_kw"
SURPLUS_STOP_IMPORT_KW: Final = 0.3  # stop overskudsopvarmning når der købes mere end dette fra nettet
SURPLUS_CONFIRM_TICKS: Final = 4     # betingelsen skal holde i 4 × 30 s (undgår at slå til/fra)
TICK_SECONDS: Final = 30
CONF_SOLAR_POWER: Final = "solar_power_sensor"
F_HOTWATER_SURPLUS_SMART: Final = "hotwater_surplus_smart"
HOTWATER_HYSTERESIS: Final = 3.0  # °C – start igen når vandet er kølet så meget af
# Varme / varmepumpe
CONF_HEATING_CLIMATE: Final = "heating_climate_entity"
CONF_HEATPUMP_ENERGY: Final = "heatpump_energy_sensor"
CONF_OUTDOOR_TEMP: Final = "outdoor_temp_sensor"
CONF_WEATHER: Final = "weather_entity"
S_HEATING_MAX_OUTDOOR: Final = "heating_max_outdoor"
HEATING_OUTDOOR_HYSTERESIS: Final = 1.0  # °C – aktiv igen når det er så meget koldere end grænsen

BATTERY_KEYS: Final = [
    CONF_BATTERY_MODE_ENTITY,
    CONF_BATTERY_OPT_CHARGE,
    CONF_BATTERY_OPT_DISCHARGE,
    CONF_BATTERY_OPT_NORMAL,
    CONF_BATTERY_SOC,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_BATTERY_SCRIPT_CHARGE,
    CONF_BATTERY_SCRIPT_DISCHARGE,
    CONF_BATTERY_SCRIPT_NORMAL,
    CONF_HUAWEI_DEVICE,
    CONF_HUAWEI_POWER,
    CONF_BATTERY_POWER,
    CONF_BATTERY_POWER_SIGN,
]
SOLAR_KEYS: Final = [CONF_SOLAR_ENERGY, CONF_EXPORT_ENERGY, CONF_SOLAR_CURTAIL, CONF_SOLAR_FORECAST,
                     CONF_GRID_POWER, CONF_GRID_POWER_SIGN, CONF_SOLAR_POWER,
                     CONF_EXPORT_OPT_STOP, CONF_EXPORT_OPT_NORMAL, CONF_EXPORT_VALUE_STOP,
                     CONF_EXPORT_VALUE_NORMAL, CONF_EXPORT_SCRIPT_STOP, CONF_EXPORT_SCRIPT_START]
EV_KEYS: Final = [CONF_EV_SWITCH, CONF_EV_ENERGY, CONF_EV_SOC]

# Flere elbiler: options["evs"] = [{"id", "name", "charger", "soc", "energy"}, ...]
CONF_EVS: Final = "evs"
S_EV_MAX_TOTAL_POWER: Final = "ev_max_total_power"


def get_cars(config: dict) -> list[dict]:
    """Elbilerne i opsætningen. En ældre opsætning med én lader bliver til bilen 'ev'."""
    cars = config.get(CONF_EVS)
    if cars:
        return [c for c in cars if c.get("charger")]
    if config.get(CONF_EV_SWITCH):
        return [{
            "id": "ev",
            "name": "Elbil",
            "charger": config.get(CONF_EV_SWITCH),
            "soc": config.get(CONF_EV_SOC),
            "energy": config.get(CONF_EV_ENERGY),
        }]
    return []
HOTWATER_KEYS: Final = [CONF_HOTWATER_ENTITY, CONF_HOTWATER_TEMP, CONF_HOTWATER_ENERGY]
HEATING_KEYS: Final = [CONF_HEATING_CLIMATE, CONF_HEATPUMP_ENERGY]
WEATHER_KEYS: Final = [CONF_OUTDOOR_TEMP, CONF_WEATHER]

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
S_BATTERY_CAPACITY: Final = "battery_capacity"
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
S_SOLAR_EXPORT_MIN: Final = "solar_export_min_price"

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
    S_BATTERY_CAPACITY: 10.0,
    S_EV_HOURS: 4.0,
    S_EV_READY_HOUR: 7.0,
    S_EV_TARGET_SOC: 80.0,
    S_EV_CAPACITY: 60.0,
    S_EV_CHARGE_POWER: 11.0,
    "ev_solar_min_a": 6.0,
    "ev_solar_max_a": 16.0,
    "ev_solar_start_kw": 4.0,
    S_EV_MAX_TOTAL_POWER: 0.0,
    S_HOTWATER_HOURS: 1.0,
    S_HOTWATER_BOOST_TEMP: 55.0,
    S_HOTWATER_NORMAL_TEMP: 45.0,
    S_HEATING_BASE_TEMP: 21.0,
    S_HEATING_BOOST_DEG: 1.0,
    S_HEATING_BOOST_HOURS: 4.0,
    S_HEATING_REDUCE_DEG: 1.0,
    S_HEATING_REDUCE_HOURS: 3.0,
    S_SOLAR_EXPORT_MIN: 0.0,
    S_BATTERY_CHARGE_FROM: 0.0,
    S_BATTERY_CHARGE_TO: 24.0,
    S_BATTERY_DISCHARGE_FROM: 0.0,
    S_BATTERY_DISCHARGE_TO: 24.0,
    S_HOTWATER_FROM: 0.0,
    S_HOTWATER_TO: 24.0,
    S_HOTWATER_SURPLUS_KW: 0.0,
    S_HEATING_BOOST_FROM: 0.0,
    S_HEATING_BOOST_TO: 24.0,
    S_HEATING_REDUCE_FROM: 0.0,
    S_HEATING_REDUCE_TO: 24.0,
    S_HEATING_MAX_OUTDOOR: 10.0,
}

# --- Handlinger ------------------------------------------------------------
ACTION_CHARGE: Final = "charge"
ACTION_DISCHARGE: Final = "discharge"
ACTION_NORMAL: Final = "normal"
ACTION_OFF: Final = "off"
HEATING_BOOST: Final = "boost"
HEATING_REDUCE: Final = "reduce"

# SmartHus-besparelse (solceller vises for sig i solcelleoverblikket)
SAVINGS_CATEGORIES: Final = [MODULE_BATTERY, MODULE_EV, "heatpump", MODULE_HOTWATER]

# Minimum tid mellem to kald til samme enhed (sekunder)
MIN_COMMAND_INTERVAL: Final = 90

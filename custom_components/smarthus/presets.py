"""Færdige opsætninger for kendte invertere/batterier (ingen Home Assistant-afhængigheder).

Kunden vælger sit anlæg, og SmartHus finder selv entiteterne i den integration, der styrer
anlægget (fx Sigenergy Local Modbus eller Solarman). Entiteterne findes ud fra deres
unique_id i entitetsregistret – det er stabilt, også når kunden har omdøbt entiteterne.

Batteristyringen beskrives som trin pr. tilstand ("charge", "discharge", "normal"):
  ("select", nøgle, mulighed)       vælg en mulighed i en rullemenu
  ("number", nøgle, værdi)          sæt et tal – "max_soc"/"min_soc" erstattes af kortets grænser
  ("switch", nøgle, True/False)     tænd/sluk
  ("time", nøgle, "HH:MM:SS")       sæt et klokkeslæt
  ("button", nøgle, None)           tryk på en knap
Et trin med nøglen markeret "?" (fx "?program_6_soc_number") springes over, hvis entiteten
ikke findes (fx færre tidsprogrammer på modellen). Værdien KEEP betyder "den værdi, entiteten
havde, da anlægget blev valgt" (fx kundens normale driftstilstand).
"""

from __future__ import annotations

from dataclasses import dataclass, field

KEEP = "__keep__"

# Roller → SmartHus' egne opsætningsnøgler (const.py)
ROLE_KEYS = {
    "soc": "battery_soc_sensor",
    "battery_power": "battery_power_sensor",
    "battery_charge_energy": "battery_charge_energy_sensor",
    "battery_discharge_energy": "battery_discharge_energy_sensor",
    "house_energy": "house_energy_sensor",
    "grid_power": "grid_power_sensor",
    "solar_power": "solar_power_sensor",
    "solar_energy": "solar_energy_sensor",
    "import_energy": "grid_import_energy_sensor",
    "export_energy": "grid_export_energy_sensor",
}

ROLE_NAMES = {
    "soc": "Batteriniveau",
    "battery_power": "Batteriets effekt",
    "battery_charge_energy": "Energi ladet i batteriet",
    "battery_discharge_energy": "Energi afladet fra batteriet",
    "house_energy": "Husets forbrug",
    "grid_power": "Effekt til/fra nettet",
    "solar_power": "Solcellernes effekt",
    "solar_energy": "Solcelleproduktion",
    "import_energy": "Købt fra nettet",
    "export_energy": "Solgt til nettet",
}


@dataclass(frozen=True)
class Preset:
    key: str
    name: str                      # vises i listen
    domain: str                    # Home Assistant-integrationens domæne
    integration: str               # navnet på integrationen
    url: str
    roles: dict[str, str] = field(default_factory=dict)        # rolle → "platform:nøgle"
    battery_sign: str | None = None                             # discharge_positive / charge_positive
    grid_sign: str | None = None                                # export_positive / import_positive
    control: tuple = ()            # varianter: ({tilstand: [trin]}, ...) – den første, der passer, bruges
    huawei: bool = False           # Huawei: indbygget styring via batteriets enhed
    export: tuple[str, float] | None = None                     # ("number:nøgle", værdi ved stop)
    note: str = ""


def _deye_programs(charging: str, soc: str) -> list[tuple]:
    steps = []
    for i in range(1, 7):
        opt = "" if i == 1 else "?"
        steps.append(("select", f"{opt}program_{i}_charging_select", charging))
        steps.append(("number", f"{opt}program_{i}_soc_number", soc))
    return steps


PRESETS: dict[str, Preset] = {p.key: p for p in (
    Preset(
        "sigenergy", "SigEnergy", "sigen", "Sigenergy Local Modbus",
        "https://github.com/TypQxQ/Sigenergy-Local-Modbus",
        roles={
            "soc": "sensor:plant_ess_soc",
            "battery_power": "sensor:plant_ess_power",
            "grid_power": "sensor:plant_grid_sensor_active_power",
            "solar_power": "sensor:plant_photovoltaic_power",
            "battery_charge_energy": "sensor:plant_accumulated_battery_charge_energy",
            "battery_discharge_energy": "sensor:plant_accumulated_battery_discharge_energy",
            "import_energy": "sensor:plant_accumulated_grid_import_energy",
            "export_energy": "sensor:plant_accumulated_grid_export_energy",
            "solar_energy": "sensor:plant_accumulated_pv_energy",
            "house_energy": "sensor:plant_accumulated_consumed_energy",
        },
        battery_sign="charge_positive", grid_sign="import_positive",
        control=({
            "charge": [("switch", "plant_remote_ems_enable", True),
                       ("select", "plant_remote_ems_control_mode", "Command Charging (Grid First)")],
            "discharge": [("switch", "plant_remote_ems_enable", True),
                          ("select", "plant_remote_ems_control_mode", "Command Discharging (ESS First)")],
            "normal": [("switch", "plant_remote_ems_enable", True),
                       ("select", "plant_remote_ems_control_mode", "Maximum Self Consumption")],
        },),
        export=("number:plant_grid_point_maximum_export_limitation", 0.0),
        note="Remote EMS slås til af SmartHus.",
    ),
    Preset(
        "deye", "Deye / Sunsynk", "solarman", "Solarman (ha-solarman)",
        "https://github.com/davidrapan/ha-solarman",
        roles={
            "soc": "sensor:battery_sensor",
            "battery_power": "sensor:battery_power_sensor",
            "grid_power": "sensor:grid_power_sensor",
            "solar_power": "sensor:pv_power_sensor",
            "battery_charge_energy": "sensor:total_battery_charge_sensor",
            "battery_discharge_energy": "sensor:total_battery_discharge_sensor",
            "import_energy": "sensor:total_energy_import_sensor",
            "export_energy": "sensor:total_energy_export_sensor",
            "solar_energy": "sensor:total_production_sensor",
            "house_energy": "sensor:total_load_consumption_sensor",
        },
        battery_sign="discharge_positive", grid_sign="import_positive",
        control=({
            # Alle seks tidsprogrammer sættes ens, så det virker hele døgnet
            "charge": _deye_programs("Grid", "max_soc"),
            "discharge": [("select", "work_mode_select", "Export First")] + _deye_programs("Disabled", "min_soc"),
            "normal": [("select", "work_mode_select", KEEP)] + _deye_programs("Disabled", "min_soc"),
        },),
        export=("number:export_surplus_power_number", 0.0),
        note="\"Time of Use\" skal være slået til på inverteren.",
    ),
    Preset(
        "solax", "SolaX", "solax_modbus", "SolaX Inverter Modbus",
        "https://github.com/wills106/homeassistant-solax-modbus",
        roles={
            "soc": "sensor:battery_capacity",
            "battery_power": "sensor:battery_power_charge",
            "grid_power": "sensor:measured_power",
            "solar_power": "sensor:pv_power_total",
            "battery_charge_energy": "sensor:battery_input_energy_total",
            "battery_discharge_energy": "sensor:battery_output_energy_total",
            "import_energy": "sensor:grid_import_total",
            "export_energy": "sensor:grid_export_total",
            "solar_energy": "sensor:total_solar_energy",
        },
        battery_sign="charge_positive", grid_sign="export_positive",
        control=({
            "charge": [("select", "charger_use_mode", "Manual Mode"), ("select", "manual_mode_select", "Force Charge")],
            "discharge": [("select", "charger_use_mode", "Manual Mode"), ("select", "manual_mode_select", "Force Discharge")],
            "normal": [("select", "manual_mode_select", "Stop Charge and Discharge"),
                       ("select", "charger_use_mode", KEEP)],
        },),
        export=("number:export_control_user_limit", 0.0),
        note="Kræver Gen4 eller nyere (driftstilstanden \"Manual Mode\").",
    ),
    Preset(
        "foxess", "FoxESS", "foxess_modbus", "FoxESS - Modbus",
        "https://github.com/nathanmarlor/foxess_modbus",
        roles={
            "soc": "sensor:battery_soc",
            "battery_power": "sensor:invbatpower",
            "grid_power": "sensor:grid_ct",
            "solar_power": "sensor:pv_power_now",
            "battery_charge_energy": "sensor:battery_charge_total",
            "battery_discharge_energy": "sensor:battery_discharge_total",
            "import_energy": "sensor:grid_consumption_energy_total",
            "export_energy": "sensor:feed_in_energy_total",
            "solar_energy": "sensor:solar_energy_total",
        },
        battery_sign="discharge_positive", grid_sign="export_positive",
        control=({
            "charge": [("select", "work_mode", "Force Charge")],
            "discharge": [("select", "work_mode", "Force Discharge")],
            "normal": [("select", "work_mode", KEEP)],
        },),
        note="Slå \"Remote control\" til i FoxESS-integrationens indstillinger, så Force Charge/Discharge findes.",
    ),
    Preset(
        "growatt", "Growatt", "solax_modbus", "SolaX Inverter Modbus (Growatt-plugin)",
        "https://github.com/wills106/homeassistant-solax-modbus",
        roles={
            "soc": "sensor:battery_soc",
            "solar_power": "sensor:pv_power_total",
            "battery_discharge_energy": "sensor:total_battery_discharge",
            "solar_energy": "sensor:total_solar_energy",
        },
        control=(
            {   # MOD/TL-XH (nyere hybrid): tidsrum 1 dækker hele døgnet
                "charge": [("select", "charger_switch", "Enabled"), ("select", "time_1_mode", "Battery First"),
                           ("select", "time_1_enabled", "Enabled"), ("time", "time_1_begin", "00:00:00"),
                           ("time", "time_1_end", "23:59:00"), ("button", "time_1_update", None)],
                "discharge": [("select", "charger_switch", "Disabled"), ("select", "time_1_mode", "Grid First"),
                              ("select", "time_1_enabled", "Enabled"), ("time", "time_1_begin", "00:00:00"),
                              ("time", "time_1_end", "23:59:00"), ("button", "time_1_update", None)],
                "normal": [("select", "charger_switch", "Disabled"), ("select", "time_1_enabled", "Disabled"),
                           ("button", "time_1_update", None)],
            },
            {   # SPH (ældre hybrid): prioritet og opladning fra nettet
                "charge": [("select", "priority", "Battery First"), ("select", "battery_first_charge_from_grid", "Enabled")],
                "discharge": [("select", "battery_first_charge_from_grid", "Disabled"), ("select", "priority", "Grid First")],
                "normal": [("select", "battery_first_charge_from_grid", "Disabled"), ("select", "priority", "Load First")],
            },
        ),
        note="Afprøv på anlægget først – Growatt-modellerne styres forskelligt.",
    ),
    Preset(
        "fronius", "Fronius", "fronius", "Fronius (indbygget i Home Assistant)",
        "https://www.home-assistant.io/integrations/fronius/",
        roles={
            "soc": "sensor:state_of_charge",
            "battery_power": "sensor:power_battery",
            "grid_power": "sensor:power_grid",
            "solar_power": "sensor:power_photovoltaics",
            "import_energy": "sensor:energy_real_consumed",
            "export_energy": "sensor:energy_real_produced",
            "solar_energy": "sensor:energy_total",
        },
        battery_sign="discharge_positive", grid_sign="import_positive",
        control=({
            # Fronius' lagerstyring: en negativ grænse vender retningen (afladegrænse −100 % = lad med fuld effekt)
            "charge": [("number", "battery_charge_power_limit", 100), ("switch", "battery_charge_power_limit_enabled", True),
                       ("switch", "battery_grid_charging", True),
                       ("number", "battery_discharge_power_limit", -100), ("switch", "battery_discharge_power_limit_enabled", True)],
            "discharge": [("number", "battery_discharge_power_limit", 100), ("switch", "battery_discharge_power_limit_enabled", True),
                          ("number", "battery_charge_power_limit", -100), ("switch", "battery_charge_power_limit_enabled", True)],
            "normal": [("switch", "battery_charge_power_limit_enabled", False),
                       ("switch", "battery_discharge_power_limit_enabled", False),
                       ("number", "battery_charge_power_limit", 100), ("number", "battery_discharge_power_limit", 100),
                       ("switch", "battery_grid_charging", False)],
        },),
        note="Batteristyring kræver Modbus TCP og \"Inverter control via Modbus\" slået til (Gen24/Tauro/Verto). "
             "Effektsensorerne \"Power battery\" og \"Power grid\" er deaktiveret som standard – slå dem til.",
    ),
    Preset(
        "solplanet", "Solplanet", "solplanet", "Solplanet",
        "https://github.com/calvinbui/home-assistant-solplanet",
        roles={"soc": "sensor:soc"},
        note="Solplanet kan ikke tvangslade direkte – batteriet styres med scripts (se README). "
             "Målerne findes automatisk.",
    ),
    Preset(
        "huawei", "Huawei", "huawei_solar", "Huawei Solar",
        "https://github.com/wlcrs/huawei_solar",
        roles={
            "soc": "sensor:storage_state_of_capacity",
            "battery_power": "sensor:storage_charge_discharge_power",
            "grid_power": "sensor:power_meter_active_power",
            "solar_power": "sensor:input_power",
            "battery_charge_energy": "sensor:storage_total_charge",
            "battery_discharge_energy": "sensor:storage_total_discharge",
            "import_energy": "sensor:grid_accumulated_energy",
            "export_energy": "sensor:grid_exported_energy",
            "solar_energy": "sensor:accumulated_yield_energy",
        },
        battery_sign="charge_positive", grid_sign="export_positive",
        huawei=True,
    ),
)}


@dataclass
class Entry:
    """Et udsnit af entitetsregistret."""
    entity_id: str
    unique_id: str
    device_id: str | None = None
    disabled: bool = False


def _find(entries: list[Entry], spec: str) -> Entry | None:
    """Find entiteten for "platform:nøgle". unique_id slutter på nøglen; ved flere (fx
    "_battery_sensor" og "_total_battery_sensor") er den korteste den rigtige."""
    platform, key = spec.split(":", 1)
    hits = [e for e in entries if e.entity_id.split(".")[0] == platform
            and (e.unique_id == key or e.unique_id.endswith("_" + key) or e.unique_id.endswith("-" + key))]
    return min(hits, key=lambda e: len(e.unique_id)) if hits else None


@dataclass
class Match:
    preset: Preset
    roles: dict[str, str]                       # rolle → entity_id
    disabled: list[str]                         # fundet, men deaktiveret
    missing: list[str]                          # roller, der ikke blev fundet
    actions: dict[str, list[dict]] | None       # tilstand → trin (med entity_id'er)
    control_missing: list[str]                  # entiteter, styringen mangler
    huawei_device: str | None = None
    export: tuple[str, float] | None = None     # (entity_id, værdi ved stop)


def match(preset: Preset, entries: list[Entry]) -> Match:
    """Find anlæggets entiteter. Batteristyringen bruger den første variant, hvor alle
    krævede entiteter findes."""
    roles, disabled, missing = {}, [], []
    found_soc = None
    for role, spec in preset.roles.items():
        e = _find(entries, spec)
        if e is None:
            missing.append(role)
        elif e.disabled:
            disabled.append(e.entity_id)
        else:
            roles[role] = e.entity_id
            if role == "soc":
                found_soc = e
    actions, control_missing = None, []
    for variant in preset.control:
        resolved, lacking = {}, []
        for state, steps in variant.items():
            out = []
            for kind, key, value in steps:
                optional = key.startswith("?")
                key = key.lstrip("?")
                plat = {"switch": "switch", "select": "select", "number": "number", "time": "time", "button": "button"}[kind]
                e = _find(entries, f"{plat}:{key}")
                if e is None or e.disabled:
                    if not optional:
                        lacking.append(f"{plat}.…{key}")
                    continue
                out.append({"type": kind, "entity_id": e.entity_id, "value": value})
            resolved[state] = out
        if not lacking:
            actions, control_missing = resolved, []
            break
        if not control_missing or len(lacking) < len(control_missing):
            control_missing = sorted(set(lacking))
    export = None
    if preset.export:
        e = _find(entries, preset.export[0])
        if e is not None and not e.disabled:
            export = (e.entity_id, preset.export[1])
    huawei_device = found_soc.device_id if preset.huawei and found_soc else None
    return Match(preset, roles, disabled, missing, actions, control_missing, huawei_device, export)


def fill_keep(actions: dict[str, list[dict]], current: dict[str, str | None], fallback: dict[str, str]) -> dict:
    """Erstat KEEP med entitetens nuværende værdi (kundens normale indstilling).

    Står entiteten allerede i en af SmartHus' egne tilstande (fx "Force Charge"), bruges
    `fallback` i stedet – ellers ville "normal" betyde fortsat tvangsladning.
    """
    ours = {s["value"] for steps in actions.values() for s in steps if s["value"] != KEEP}
    out = {}
    for state, steps in actions.items():
        new = []
        for s in steps:
            if s["value"] == KEEP:
                cur = current.get(s["entity_id"])
                val = cur if cur and cur not in ours and cur not in ("unknown", "unavailable") else fallback.get(s["entity_id"])
                if not val:
                    continue
                s = {**s, "value": val}
            new.append(s)
        out[state] = new
    return out


# Hvad "normal" sættes til, hvis den nuværende værdi ikke kan bruges
KEEP_FALLBACK = {"work_mode_select": "Zero Export To CT", "charger_use_mode": "Self Use Mode", "work_mode": "Self Use"}


def keep_fallbacks(actions: dict[str, list[dict]], entries: list[Entry]) -> dict[str, str]:
    by_entity = {}
    uid = {e.entity_id: e.unique_id for e in entries}
    for steps in actions.values():
        for s in steps:
            if s["value"] == KEEP:
                for key, val in KEEP_FALLBACK.items():
                    u = uid.get(s["entity_id"], "")
                    if u == key or u.endswith("_" + key):
                        by_entity[s["entity_id"]] = val
    return by_entity

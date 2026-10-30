"""Færdige opsætninger for kendte ladestandere (ingen Home Assistant-afhængigheder).

Kunden vælger sin ladestander og selve laderen (enheden), og SmartHus finder entiteterne i den
integration, der styrer den. Entiteterne findes ud fra deres unique_id i entitetsregistret –
det er stabilt, også når kunden har omdøbt dem.

Nøgler skrives som "platform:nøgle" (unique_id slutter på nøglen, eller begynder med "nøgle-")
eller "platform:~regex" (søges i unique_id), som for invertere i presets.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .presets import Entry

# Roller → felter i bilens opsætning
ROLE_FIELDS = {
    "charger": "charger",
    "plug": "plug",
    "power": "power",
    "energy": "energy",
    "current": "current_entity",
}

ROLE_NAMES = {
    "charger": "Tænd/sluk opladning",
    "plug": "Kabel tilsluttet",
    "power": "Ladeeffekt",
    "energy": "Energimåler",
    "current": "Ladestrøm (til solcelleladning)",
}


@dataclass(frozen=True)
class ChargerPreset:
    key: str
    name: str
    domain: str
    integration: str
    url: str
    roles: dict[str, str] = field(default_factory=dict)
    charger_on: str | None = None        # rullemenu: mulighed for "lad"
    charger_off: str | None = None       # rullemenu: mulighed for "stop"
    plug_off: tuple[str, ...] = ()       # tilstande, der betyder "ingen bil" for netop denne lader
    entry_wide: tuple[str, ...] = ()     # roller, der må findes uden for selve laderen (fx Zaptec-installationen)
    easee: bool = False                  # Easee: ladestrøm og pause via laderens enhed
    note: str = ""


CHARGERS: dict[str, ChargerPreset] = {c.key: c for c in (
    ChargerPreset(
        "easee", "Easee", "easee", "Easee", "https://github.com/nordicopen/easee_hass",
        roles={"charger": "switch:is_enabled", "plug": "sensor:status", "power": "sensor:power",
               "energy": "sensor:lifetime_energy"},
        easee=True,
        note="Ladestrømmen styres direkte via Easee (dynamisk ladegrænse).",
    ),
    ChargerPreset(
        "zaptec", "Zaptec", "zaptec", "Zaptec", "https://github.com/custom-components/zaptec",
        roles={"charger": "switch:charger_operation_mode", "plug": "sensor:charger_operation_mode",
               "power": "sensor:total_charge_power", "energy": "sensor:signed_meter_value_kwh",
               "current": "number:available_current"},
        entry_wide=("current",),
        note="Ladestrømmen styres via installationens 'tilgængelige strøm' – det påvirker alle ladere i installationen.",
    ),
    ChargerPreset(
        "wallbox", "Wallbox", "wallbox", "Wallbox (indbygget)", "https://www.home-assistant.io/integrations/wallbox/",
        roles={"charger": "switch:paused", "plug": "sensor:status_description", "power": "sensor:charging_power",
               "energy": "sensor:added_energy", "current": "number:max_charging_current"},
        plug_off=("ready", "locked", "updating"),
        note="Energimåleren tæller pr. opladning (nulstilles ved ny opladning).",
    ),
    ChargerPreset(
        "wattpilot", "Fronius Wattpilot", "wattpilot", "Fronius Wattpilot",
        "https://github.com/mk-maddin/wattpilot-HA",
        roles={"charger": "select:frc", "plug": "sensor:car", "power": "sensor:nrg", "energy": "sensor:eto",
               "current": "number:amp"},
        charger_on="Forced on", charger_off="Stopped",
        note="'Force State' er slået fra som standard i Wattpilot-integrationen – slå den til først.",
    ),
    ChargerPreset(
        "goe", "go-e Charger", "goecharger_api2", "go-e Charger (API v2)",
        "https://github.com/marq24/ha-goecharger-api2",
        roles={"charger": "select:frc", "plug": "sensor:car_value", "power": "sensor:nrg_11", "energy": "sensor:eto",
               "current": "number:amp"},
        charger_on="2", charger_off="1",
    ),
    ChargerPreset(
        "garo", "Garo", "garo_wallbox", "Garo Wallbox", "https://github.com/sockless-coding/garo_wallbox",
        roles={"charger": "select:sensor", "plug": "sensor:status", "power": "sensor:current_charging_power",
               "energy": "sensor:acc_energy", "current": "number:current_limit"},
        charger_on="ALWAYS_ON", charger_off="ALWAYS_OFF",
    ),
    ChargerPreset(
        "chargeamps", "Charge Amps", "chargeamps", "Charge Amps", "https://github.com/kirei/hass-chargeamps",
        roles={"charger": "switch:enable", "plug": "binary_sensor:cable_connected", "power": "sensor:power",
               "energy": "sensor:total_energy", "current": "number:max_current"},
    ),
    ChargerPreset(
        "alfen", "Alfen", "alfen_wallbox", "Alfen Wallbox", "https://github.com/leeyuentuen/alfen_wallbox",
        roles={"charger": "switch:charging", "plug": "sensor:status_socket_1",
               "power": "sensor:active_power_total_socket_1", "energy": "sensor:meter_reading_socket_1"},
        note="Alfen sætter laderens strøm til 0 for at stoppe, så solcelleladning med varierende strøm bruges ikke.",
    ),
    ChargerPreset(
        "monta", "Monta", "monta", "Monta", "https://github.com/nickknissen/hass-monta",
        roles={"charger": "switch:charger", "plug": "binary_sensor:cable_plugged_in",
               "energy": "sensor:charger_lastMeterReadingKwh"},
        note="Monta har ingen effektmåler – vælg evt. laderens effekt fra en anden måler.",
    ),
    ChargerPreset(
        "ocpp", "OCPP (fx ABB, Etrel, CTEK, Kempower)", "ocpp", "OCPP", "https://github.com/lbbrhzn/ocpp",
        roles={"charger": r"switch:~\.charge_control$", "plug": r"sensor:~[._]status_connector[._]sensor$",
               "power": r"sensor:~[._]power_active_import[._]sensor$",
               "energy": r"sensor:~[._]energy_active_import_register[._]sensor$",
               "current": r"number:~\.maximum_current$"},
    ),
)}


def find(entries: list[Entry], spec: str) -> Entry | None:
    """Find entiteten for "platform:nøgle" eller "platform:~regex". Ved flere er den korteste den rigtige."""
    platform, key = spec.split(":", 1)
    cands = [e for e in entries if e.entity_id.split(".")[0] == platform]
    if key.startswith("~"):
        rx = re.compile(key[1:], re.IGNORECASE)
        hits = [e for e in cands if rx.search(e.unique_id)]
    else:
        hits = [e for e in cands if e.unique_id == key or e.unique_id.endswith(("_" + key, "-" + key))
                or e.unique_id.startswith(key + "-")]
    return min(hits, key=lambda e: len(e.unique_id)) if hits else None


@dataclass
class ChargerMatch:
    preset: ChargerPreset
    fields: dict[str, str]          # bilens felter → entity_id
    disabled: list[str]
    missing: list[str]              # roller der ikke blev fundet


def match(preset: ChargerPreset, device_entries: list[Entry], entry_entries: list[Entry] | None = None) -> ChargerMatch:
    """Find laderens entiteter blandt enhedens entiteter (og for enkelte roller i hele integrationen)."""
    fields, disabled, missing = {}, [], []
    for role, spec in preset.roles.items():
        e = find(device_entries, spec)
        if e is None and role in preset.entry_wide and entry_entries:
            e = find(entry_entries, spec)
        if e is None:
            missing.append(role)
        elif e.disabled:
            disabled.append(e.entity_id)
            missing.append(role)
        else:
            fields[ROLE_FIELDS[role]] = e.entity_id
    return ChargerMatch(preset, fields, disabled, missing)


def car_fields(m: ChargerMatch, device_id: str | None) -> dict:
    """Felterne der gemmes på bilen (ud over de fundne entiteter)."""
    p = m.preset
    out: dict = dict(m.fields)
    out["charger_preset"] = p.key
    out["charger_on"] = p.charger_on
    out["charger_off"] = p.charger_off
    out["plug_off"] = list(p.plug_off) or None
    if p.easee:
        out["easee_device"] = device_id
    return out

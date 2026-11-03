"""Growatt via skyen (Home Assistants indbyggede integration "Growatt Server" med API-token).

Inverteren styres med faste tidsrum i stedet for en driftstilstand: SmartHus skriver det tidsrum,
planen vil lade eller aflade i, og slår tidsrummene fra igen i normal drift. Fordi Growatt selv
kender sluttidspunktet, stopper inverteren også til tiden, hvis Home Assistant eller nettet
forsvinder midt i en blok.

- SPH (hybrid med batteri): write_ac_charge_times / write_ac_discharge_times
  "Gem batteriet" er et ladetidsrum (Battery First) med ladeeffekt 1 % og uden netopladning: batteriet aflader
  ikke, og huset kører på nettet. Afprøvet på SPH: med 100 % ladede det alligevel fra nettet (op til 1,5 kW),
  og Grid First med afladestop 100 % lod batteriet dække huset – med 1 % står batteriet stille.
- MIN TL-XH: update_time_segment (tidsrum 1: Battery First / Grid First)
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

DOMAIN = "growatt_server"
TYPE_SPH = "sph"
TYPE_MIN = "min"

HOLD_CHARGE_POWER = 1   # % – "gem batteriet" på SPH: Battery First med (næsten) ingen opladning
_OFF3 = {"period_1_enabled": False, "period_2_enabled": False, "period_3_enabled": False}


def device_type(unique_id: str) -> str | None:
    """SPH-sensorer hedder "mix_…"/"sph_…", MIN TL-XH hedder "tlx_…"."""
    key = unique_id.rsplit("-", 1)[-1]
    if key.startswith(("mix_", "sph_")):
        return TYPE_SPH
    if key.startswith("tlx_"):
        return TYPE_MIN
    return None


def _hhmm(t: datetime, tz: tzinfo) -> str:
    return t.astimezone(tz).strftime("%H:%M")


def block_window(slots: list, cur, tz: tzinfo) -> tuple[str, str]:
    """Lokalt start/slut (HH:MM) for den sammenhængende blok i `slots`, der indeholder `cur`.

    Blokken klippes til `cur`s døgn, da Growatts tidsrum ikke kan gå hen over midnat. Næste døgns
    del bliver sin egen blok og sendes, når klokken passerer midnat. Ligger `cur` ikke i `slots`
    (fx lad, fordi salgsprisen er for lav), bruges kun det aktuelle tidsrum.
    """
    by_start = {s.start: s for s in slots}
    first = last = cur
    if cur.start in by_start:
        while (p := by_start.get(first.start - (first.end - first.start))) and p.end == first.start:
            first = p
        while (n := by_start.get(last.end)) is not None:
            last = n
    day_start = cur.start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    start = max(first.start.astimezone(tz), day_start)
    end = min(last.end.astimezone(tz), day_end)
    end_s = "23:59" if end >= day_end else _hhmm(end, tz)
    return _hhmm(start, tz), end_s


def commands(kind: str, device: str, action: str, window: tuple[str, str] | None,
             max_soc: int, min_soc: int, power: int) -> list[tuple[str, dict]]:
    """Handlingerne (service, data), der bringer inverteren i `action`. Det, der skal slås fra,
    sendes først, så lade- og afladetidsrum aldrig er aktive på samme tid."""
    start, end = window or ("00:00", "23:59")
    if kind == TYPE_MIN:
        if action == "charge":
            return [("update_time_segment", {"device_id": device, "segment_id": 1, "batt_mode": "battery_first",
                                             "start_time": start, "end_time": end, "enabled": True})]
        if action == "discharge":
            return [("update_time_segment", {"device_id": device, "segment_id": 1, "batt_mode": "grid_first",
                                             "start_time": start, "end_time": end, "enabled": True})]
        return [("update_time_segment", {"device_id": device, "segment_id": 1, "batt_mode": "load_first",
                                         "start_time": "00:00", "end_time": "23:59", "enabled": False})]

    # Ladeeffekten sættes tilbage til 100 %, så 1 % fra en hold-periode ikke hænger ved og bremser solopladningen
    charge_off = ("write_ac_charge_times", {"device_id": device, "charge_power": 100, "mains_enabled": False, **_OFF3})
    discharge_off = ("write_ac_discharge_times", {"device_id": device, **_OFF3})
    if action == "charge":
        return [discharge_off, ("write_ac_charge_times", {
            "device_id": device, "charge_power": power, "charge_stop_soc": max_soc, "mains_enabled": True,
            **_OFF3, "period_1_start": start, "period_1_end": end, "period_1_enabled": True})]
    if action == "hold":
        # Battery First med ladeeffekt 1 % og uden netopladning: batteriet aflader ikke, og der lades næsten intet
        # (SPH lader fra nettet i Battery First, selv med netopladning slået fra – 1 % holder det på ca. 0 W)
        return [discharge_off, ("write_ac_charge_times", {
            "device_id": device, "charge_power": HOLD_CHARGE_POWER, "charge_stop_soc": max_soc, "mains_enabled": False,
            **_OFF3, "period_1_start": start, "period_1_end": end, "period_1_enabled": True})]
    if action == "discharge":
        return [charge_off, ("write_ac_discharge_times", {
            "device_id": device, "discharge_power": power, "discharge_stop_soc": min_soc,
            **_OFF3, "period_1_start": start, "period_1_end": end, "period_1_enabled": True})]
    return [charge_off, discharge_off]

"""Vejr og solprognose (ingen Home Assistant-afhængigheder, så det kan testes)."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

# Foretrukne vejr-entiteter: Met.no opretter "weather.forecast_home" (tidligere "weather.home")
PREFERRED_WEATHER = ("weather.forecast_home", "weather.home")

# Kendte sensorer fra Forecast.Solar og Solcast
FORECAST_SENSORS = {
    "forecast_solar": {
        "today": "sensor.energy_production_today",
        "remaining": "sensor.energy_production_today_remaining",
        "tomorrow": "sensor.energy_production_tomorrow",
        "power_now": "sensor.power_production_now",
    },
    "solcast": {
        "today": "sensor.solcast_pv_forecast_forecast_today",
        "remaining": "sensor.solcast_pv_forecast_forecast_remaining_today",
        "tomorrow": "sensor.solcast_pv_forecast_forecast_tomorrow",
        "power_now": "sensor.solcast_pv_forecast_power_now",
    },
}


def pick_weather_entity(entity_ids: list[str]) -> str | None:
    """Vælg vejr-entiteten fra skyen: Met.no's 'hjem' først, ellers den første."""
    ids = sorted(e for e in entity_ids if e.startswith("weather."))
    for pref in PREFERRED_WEATHER:
        if pref in ids:
            return pref
    return ids[0] if ids else None


_OM_REMAINING = "energy_production_today_remaining"   # Forecast.Solar og Open-Meteo Solar Forecast (fx sensor.hus_…)


def _sibling_ids(remaining_id: str, existing: set[str]) -> dict[str, str]:
    """Open-Meteo Solar Forecast navngiver sensorerne efter opsætningens navn: sensor.<navn>_energy_production_…"""
    prefix = remaining_id[: -len(_OM_REMAINING)]
    names = {"today": "energy_production_today", "remaining": _OM_REMAINING,
             "tomorrow": "energy_production_tomorrow", "power_now": "power_production_now"}
    return {k: prefix + v for k, v in names.items() if prefix + v in existing or k == "remaining"}


def forecast_sensor_ids(remaining_id: str | None, existing: set[str]) -> dict[str, str]:
    """Find prognose-sensorerne: ud fra den valgte 'resterende i dag'-sensor, ellers kendte navne."""
    if remaining_id and remaining_id.endswith(_OM_REMAINING) and \
            not remaining_id.startswith(FORECAST_SENSORS["forecast_solar"]["remaining"]):
        return _sibling_ids(remaining_id, existing)
    for names in FORECAST_SENSORS.values():
        base = names["remaining"]
        if remaining_id:
            if not remaining_id.startswith(base):
                continue
            suffix = remaining_id[len(base):]      # fx _2 for en ekstra panelflade
        else:
            suffix = ""
        found = {k: v + suffix for k, v in names.items() if v + suffix in existing}
        if remaining_id:
            found["remaining"] = remaining_id
        if found:
            return found
    if remaining_id:
        return {"remaining": remaining_id}
    # Intet valgt og ingen kendte navne: Open-Meteo Solar Forecast (sensor.<navn>_energy_production_today_remaining)
    om = sorted(e for e in existing if e.endswith("_" + _OM_REMAINING))
    return _sibling_ids(om[0], existing) if om else {}


def summarize_forecast(wh_hours: dict[str, float], now: datetime, tz: tzinfo,
                       key_is_end: bool = False) -> dict:
    """Saml en prognose (Wh pr. periode) til i dag, resten af dagen, i morgen og en timekurve for i dag.

    key_is_end: Forecast.Solar angiver periodens slutning (fx solopgang 07:52, 08:00, 09:00 …),
    Solcast periodens start.
    """
    points = []
    for key, wh in wh_hours.items():
        try:
            t = datetime.fromisoformat(str(key))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=tz)
        points.append((t, float(wh or 0)))
    points.sort()
    if not points:
        return {}
    hour, limit = timedelta(hours=1), timedelta(hours=3)
    local_now = now.astimezone(tz)
    today, tomorrow = local_now.date(), local_now.date() + timedelta(days=1)
    today_wh = remaining_wh = tomorrow_wh = 0.0
    buckets: dict[datetime, float] = {}
    for i, (t, wh) in enumerate(points):
        if key_is_end:
            end = t
            start = points[i - 1][0] if i > 0 and t - points[i - 1][0] <= limit else t - hour
        else:
            start = t
            end = points[i + 1][0] if i + 1 < len(points) and points[i + 1][0] - t <= limit else t + hour
        if end <= start:
            continue
        d = start.astimezone(tz).date()
        if d == tomorrow:
            tomorrow_wh += wh
            continue
        if d != today:
            continue
        today_wh += wh
        if end > now:
            share = 1.0 if start >= now else (end - now).total_seconds() / (end - start).total_seconds()
            remaining_wh += wh * share
        h0 = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        buckets[h0] = buckets.get(h0, 0.0) + wh
    curve = [{"start": h.isoformat(), "kw": round(w / 1000, 2)} for h, w in sorted(buckets.items())]
    peak = max(curve, key=lambda c: c["kw"], default=None)
    return {
        "today_kwh": round(today_wh / 1000, 2),
        "remaining_kwh": round(remaining_wh / 1000, 2),
        "tomorrow_kwh": round(tomorrow_wh / 1000, 2) if tomorrow_wh else None,
        "peak_time": peak["start"] if peak and peak["kw"] > 0 else None,
        "peak_kw": peak["kw"] if peak and peak["kw"] > 0 else None,
        "hours": curve,
    }


def merge_wh(*sources: dict[str, float]) -> dict[str, float]:
    """Læg flere prognoser sammen (fx to panelflader i Forecast.Solar)."""
    out: dict[str, float] = {}
    for src in sources:
        for k, v in (src or {}).items():
            out[k] = out.get(k, 0.0) + float(v or 0)
    return out


def solar_by_hour(wh_hours: dict[str, float], tz: tzinfo, key_is_end: bool = False) -> dict[datetime, float]:
    """Prognosen som kWh pr. lokal time (nøgle: timens start). Bruges til at regne med, at solen lader batteriet."""
    points = []
    for key, wh in wh_hours.items():
        try:
            t = datetime.fromisoformat(str(key))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=tz)
        points.append((t, float(wh or 0)))
    points.sort()
    out: dict[datetime, float] = {}
    hour, limit = timedelta(hours=1), timedelta(hours=3)
    for i, (t, wh) in enumerate(points):
        if key_is_end:
            start = points[i - 1][0] if i > 0 and t - points[i - 1][0] <= limit else t - hour
        else:
            start = t
        h = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        out[h] = out.get(h, 0.0) + wh / 1000
    return out


class SunTracker:
    """Sammenligner solcellernes faktiske produktion i dag med prognosen for de samme timer.

    Giver solen f.eks. kun 15 % af prognosen indtil nu, regnes der også kun med 15 % resten af dagen.
    Først når prognosen indtil nu er mindst `min_kwh`, bruges forholdet – før det er det for usikkert.
    Nulstilles ved midnat (og ved genstart).
    """

    def __init__(self, min_kwh: float = 0.3) -> None:
        self.day = None
        self.measured = 0.0
        self.forecast = 0.0
        self.last: datetime | None = None
        self.min_kwh = min_kwh

    def update(self, now: datetime, measured_kw: float | None, forecast_kw: float | None) -> None:
        if self.day != now.date():
            self.day, self.measured, self.forecast, self.last = now.date(), 0.0, 0.0, None
        if self.last is not None and measured_kw is not None and forecast_kw is not None:
            hours = min(max((now - self.last).total_seconds(), 0.0), 180.0) / 3600   # højst 3 min pr. skridt
            self.measured += max(measured_kw, 0.0) * hours
            self.forecast += max(forecast_kw, 0.0) * hours
        self.last = now

    @property
    def ratio(self) -> float | None:
        """Faktisk ÷ prognose indtil nu i dag (None, hvis det endnu er for usikkert)."""
        if self.forecast < self.min_kwh:
            return None
        return min(max(self.measured / self.forecast, 0.05), 1.5)

    def factor(self, base: float) -> float:
        """Andel af prognosen, der regnes med resten af i dag: aldrig mere end `base`, mindre hvis solen svigter."""
        r = self.ratio
        return base if r is None else min(base, r)

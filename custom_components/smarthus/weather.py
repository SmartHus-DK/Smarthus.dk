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


def forecast_sensor_ids(remaining_id: str | None, existing: set[str]) -> dict[str, str]:
    """Find prognose-sensorerne: ud fra den valgte 'resterende i dag'-sensor, ellers kendte navne."""
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
    return {"remaining": remaining_id} if remaining_id else {}


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

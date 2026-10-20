"""Husets forbrugsmønster og batteriets reserve.

Ren logik uden Home Assistant, så den kan testes direkte.

* Profilen er det gennemsnitlige forbrug (kWh) pr. time på døgnet,
  delt i hverdage og weekend, lært ud fra de seneste dages historik.
* Reserven er det forbrug, huset forventes at have fra nu og frem til
  næste billige ladeperiode, minus forventet solproduktion.
  Det, batteriet har ud over reserven (og laveste niveau), må sælges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

LEARN_DAYS = 14
MIN_DAYS = 2  # så få dage med data før profilen bruges
MAX_HOUR_KWH = 50.0  # spring urealistiske timer over (fx nulstillet måler)
SUN_START, SUN_END = 6, 21  # timer hvor solceller regnes at producere (lokal tid)
EFFICIENCY = 0.95  # tab ved afladning


@dataclass
class Profile:
    weekday: list[float] = field(default_factory=lambda: [0.0] * 24)
    weekend: list[float] = field(default_factory=lambda: [0.0] * 24)
    days: int = 0

    @property
    def ready(self) -> bool:
        return self.days >= MIN_DAYS

    def hours_for(self, day: datetime) -> list[float]:
        return self.weekend if day.weekday() >= 5 else self.weekday

    def as_dict(self) -> dict:
        return {"weekday": [round(v, 3) for v in self.weekday],
                "weekend": [round(v, 3) for v in self.weekend], "days": self.days}


def build_profile(hours: list[tuple[datetime, float]]) -> Profile:
    """Byg profilen ud fra (lokal timestart, kWh i timen)."""
    sums = {"weekday": [0.0] * 24, "weekend": [0.0] * 24}
    counts = {"weekday": [0] * 24, "weekend": [0] * 24}
    valid = 0
    for start, kwh in hours:
        if kwh is None or kwh < 0 or kwh > MAX_HOUR_KWH:
            continue
        kind = "weekend" if start.weekday() >= 5 else "weekday"
        sums[kind][start.hour] += kwh
        counts[kind][start.hour] += 1
        valid += 1
    p = Profile(days=valid // 24)  # hele døgn med data
    all_avg = [
        (sums["weekday"][h] + sums["weekend"][h]) / max(counts["weekday"][h] + counts["weekend"][h], 1)
        for h in range(24)
    ]
    for kind in ("weekday", "weekend"):
        vals = [sums[kind][h] / counts[kind][h] if counts[kind][h] else all_avg[h] for h in range(24)]
        setattr(p, kind, vals)
    return p


def expected_load(profile: Profile, start: datetime, end: datetime) -> float:
    """Forventet forbrug (kWh) mellem to lokale tidspunkter."""
    if end <= start:
        return 0.0
    total = 0.0
    t = start
    while t < end:
        hour_start = t.replace(minute=0, second=0, microsecond=0)
        nxt = min(hour_start + timedelta(hours=1), end)
        frac = (nxt - t).total_seconds() / 3600
        total += profile.hours_for(t)[t.hour] * frac
        t = nxt
    return total


def expected_solar(remaining_today: float | None, now: datetime, until: datetime) -> float:
    """Den del af dagens resterende solproduktion der falder før 'until'.

    Solprognoser (Solcast, Forecast.Solar) giver typisk kun 'resterende i dag'.
    Den fordeles jævnt over de resterende soltimer i dag – et forsigtigt skøn.
    """
    if not remaining_today or remaining_today <= 0:
        return 0.0
    day_end = now.replace(hour=SUN_END, minute=0, second=0, microsecond=0)
    sun_from = max(now, now.replace(hour=SUN_START, minute=0, second=0, microsecond=0))
    if day_end <= sun_from:
        return 0.0
    covered_end = min(until, day_end)
    if covered_end <= sun_from:
        return 0.0
    return remaining_today * (covered_end - sun_from).total_seconds() / (day_end - sun_from).total_seconds()


@dataclass
class Reserve:
    until: datetime | None = None
    load_kwh: float = 0.0
    solar_kwh: float = 0.0
    reserve_kwh: float = 0.0
    battery_kwh: float | None = None
    floor_kwh: float = 0.0
    sellable_kwh: float | None = None

    def as_dict(self) -> dict:
        return {
            "until": self.until.isoformat() if self.until else None,
            "expected_house_kwh": round(self.load_kwh, 2),
            "expected_solar_kwh": round(self.solar_kwh, 2),
            "reserve_kwh": round(self.reserve_kwh, 2),
            "battery_kwh": round(self.battery_kwh, 2) if self.battery_kwh is not None else None,
            "min_level_kwh": round(self.floor_kwh, 2),
            "sellable_kwh": round(self.sellable_kwh, 2) if self.sellable_kwh is not None else None,
        }


def calc_reserve(profile: Profile, now: datetime, until: datetime, *, soc: float | None,
                 capacity: float, min_soc: float, solar_remaining: float | None) -> Reserve:
    """Reserve til huset frem til 'until' og hvor meget der må sælges nu."""
    load = expected_load(profile, now, until)
    solar = expected_solar(solar_remaining, now, until)
    need = max(load - solar, 0.0) / EFFICIENCY
    r = Reserve(until=until, load_kwh=load, solar_kwh=solar, reserve_kwh=need,
                floor_kwh=capacity * min_soc / 100)
    if soc is not None and capacity > 0:
        r.battery_kwh = capacity * soc / 100
        r.sellable_kwh = max(r.battery_kwh - r.floor_kwh - need, 0.0)
    return r


def solar_surplus(profile: Profile, day: datetime, forecast_kwh: float | None, from_hour: int = SUN_START) -> float:
    """Forventet solcelleoverskud (kWh) en dag: prognose minus husets forbrug i soltimerne."""
    if not forecast_kwh or forecast_kwh <= 0:
        return 0.0
    hours = profile.hours_for(day)
    load = sum(hours[h] for h in range(max(from_hour, SUN_START), SUN_END))
    return max(forecast_kwh - load, 0.0)


def grid_charge_target(*, capacity: float, min_soc: float, max_soc: float, surplus_kwh: float,
                       until_sun_kwh: float) -> float:
    """Hvor mange kWh batteriet bør have efter netopladning, når solen fylder resten.

    Mindst laveste niveau + husets forbrug indtil solen giver overskud – højst fuldt (maks. niveau).
    """
    full = capacity * max_soc / 100
    floor = capacity * min_soc / 100
    return min(full, max(floor + until_sun_kwh / EFFICIENCY, full - surplus_kwh))

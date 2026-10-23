"""Varmt vand efter behov: forudsig hvornår vandet bliver for koldt, og varm op i den billigste time inden.

Mange varmepumper varmer selv vandet op, når det falder under deres egen grænse (fx 42 °C) – også
midt i den dyreste time. Når vandet er under "hold øje"-grænsen (fx 45 °C), regnes afkølingen ud
fra kurvens hældning, og der vælges den billigste time, før vandet når den laveste temperatur.
Ingen Home Assistant-afhængigheder, så det kan testes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .planner import Slot, cheapest

RISE_RESET = 0.3          # °C – en stigning betyder opvarmning: hældningen måles fra toppen
MIN_SPAN = timedelta(minutes=30)
WINDOW = timedelta(hours=4)
MIN_RATE = 0.05           # °C/time – langsommere end det regnes som "falder ikke"
MAX_AHEAD = timedelta(hours=36)


def cooling_rate(samples: list[tuple[datetime, float]], now: datetime) -> float | None:
    """Afkøling i °C pr. time (positiv = vandet bliver koldere), siden seneste opvarmning.

    samples: (tidspunkt, temperatur) i stigende rækkefølge. None, hvis der er for få målinger.
    """
    pts = [(t, v) for t, v in samples if now - t <= WINDOW]
    start = 0
    for i in range(1, len(pts)):
        # Stiger temperaturen (opvarmning), eller er den ved toppen, måles afkølingen herfra
        if pts[i][1] > pts[i - 1][1] + RISE_RESET or pts[i][1] >= pts[start][1]:
            start = i
    pts = pts[start:]
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < MIN_SPAN:
        return None
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() / 3600 for t, _ in pts]
    ys = [v for _, v in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return max(-slope, 0.0)


def plan_demand(slots: list[Slot], now: datetime, temp: float | None, rate: float | None,
                watch: float, floor: float, hours: float, planned: list[Slot]) -> tuple[list[Slot], datetime | None]:
    """Vælg ekstra opvarmning: (kvarterer, forventet tidspunkt hvor vandet når `floor`).

    Kun når vandet er under `watch`. Ligger den almindelige opvarmning (`planned`) allerede før
    tidspunktet, gøres intet ekstra.
    """
    if watch <= 0 or temp is None or temp >= watch or not slots:
        return [], None
    if temp <= floor:
        deadline = now
    elif rate is None or rate < MIN_RATE:
        return [], None
    else:
        deadline = now + timedelta(hours=(temp - floor) / rate)
    if deadline - now > MAX_AHEAD:
        return [], deadline
    if any(s.end > now and s.start < deadline for s in planned):
        return [], deadline              # den planlagte opvarmning når det i tide
    cand = [s for s in slots if s.end > now and s.start < deadline]
    if not cand:
        cand = [s for s in slots if s.start <= now < s.end]   # for sent – varm nu
    if not cand:
        return [], deadline
    return cheapest(cand, max(hours, 0.25), contiguous=True), deadline

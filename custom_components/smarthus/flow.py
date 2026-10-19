"""Energiflow lige nu mellem net, sol, batteri, hus og elbiler (ingen Home Assistant-afhængigheder)."""

from __future__ import annotations


def to_kw(value: float | None, unit: str | None) -> float | None:
    """Omregn en effekt til kW ud fra enheden (W er standard)."""
    if value is None:
        return None
    u = (unit or "W").strip().lower()
    if u == "kw":
        return value
    if u == "mw":
        return value * 1000
    return value / 1000


def energy_flow(solar_kw: float | None, export_kw: float | None, battery_kw: float | None,
                ev_kw: dict[str, float | None]) -> dict:
    """Beregn husets forbrug og flowet mellem enhederne.

    solar_kw:   produktion (≥ 0)
    export_kw:  eksport til nettet (negativ = køb fra nettet)
    battery_kw: afladning (negativ = opladning)
    ev_kw:      effekt pr. elbil
    Husets forbrug = sol + køb − eksport + afladning − opladning − elbiler.
    """
    solar = max(solar_kw or 0.0, 0.0)
    grid_in = -(export_kw or 0.0)              # positiv = køb
    battery = battery_kw or 0.0                # positiv = afladning
    ev_total = sum(max(v or 0.0, 0.0) for v in ev_kw.values())
    total = solar + grid_in + battery
    house = max(total - ev_total, 0.0)
    return {
        "solar_kw": round(solar, 2) if solar_kw is not None else None,
        "grid_kw": round(grid_in, 2) if export_kw is not None else None,
        "battery_kw": round(battery, 2) if battery_kw is not None else None,
        "house_kw": round(house, 2) if export_kw is not None else None,
        "ev_kw": {k: (round(max(v, 0.0), 2) if v is not None else None) for k, v in ev_kw.items()},
    }

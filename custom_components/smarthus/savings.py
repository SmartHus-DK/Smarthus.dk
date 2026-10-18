"""Beregning og lagring af besparelser."""

from __future__ import annotations

from datetime import datetime

from .const import SAVINGS_CATEGORIES
from .planner import Slot

# Spring urealistiske hop over (fx når en måler nulstilles) – kWh pr. minut
MAX_DELTA_KWH = 5.0


def empty() -> dict:
    zero = {c: 0.0 for c in SAVINGS_CATEGORIES}
    return {"day": None, "month": None, "today": dict(zero), "month_values": dict(zero),
            "total": dict(zero), "last": {}}


class SavingsCalculator:
    """Beregner besparelser ud fra energimålere.

    * Elbil og varmepumpe: kWh × (dagens gennemsnitspris − aktuel pris)
      → hvad det sparer at flytte forbruget til billige timer.
    * Batteri: afladet kWh × aktuel pris − opladet kWh × aktuel pris.
    * Solceller: egetforbrug × købspris + eksport × salgspris.
    """

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or empty()
        for key, val in empty().items():
            self.data.setdefault(key, val)

    def _delta(self, key: str, value: float | None) -> float:
        if value is None:
            return 0.0
        last = self.data["last"].get(key)
        self.data["last"][key] = value
        if last is None:
            return 0.0
        d = value - last
        if d < 0 or d > MAX_DELTA_KWH:
            return 0.0
        return d

    def update(self, now_local: datetime, slot: Slot | None, day_avg: float | None,
               readings: dict[str, float | None]) -> None:
        day = now_local.date().isoformat()
        month = now_local.strftime("%Y-%m")
        if self.data["day"] != day:
            self.data["day"] = day
            self.data["today"] = {c: 0.0 for c in SAVINGS_CATEGORIES}
        if self.data["month"] != month:
            self.data["month"] = month
            self.data["month_values"] = {c: 0.0 for c in SAVINGS_CATEGORIES}

        deltas = {k: self._delta(k, v) for k, v in readings.items()}
        if slot is None:
            return
        price, sale = slot.price, slot.sale
        ref = day_avg if day_avg is not None else price
        gained = {c: 0.0 for c in SAVINGS_CATEGORIES}
        ev_kwh = sum(v for k, v in deltas.items() if k == "ev" or k.startswith("ev:"))
        gained["ev"] = ev_kwh * (ref - price)
        gained["heatpump"] = deltas.get("heatpump", 0.0) * (ref - price)
        gained["battery"] = (deltas.get("battery_discharge", 0.0) - deltas.get("battery_charge", 0.0)) * price
        solar = deltas.get("solar", 0.0)
        if "export" in readings and readings["export"] is not None:
            export = min(deltas.get("export", 0.0), solar) if solar else 0.0
            gained["solar"] = max(solar - export, 0.0) * price + export * sale
        else:
            gained["solar"] = solar * price
        for c, v in gained.items():
            for bucket in ("today", "month_values", "total"):
                self.data[bucket][c] = round(self.data[bucket].get(c, 0.0) + v, 4)

    def value(self, bucket: str, category: str | None = None) -> float:
        vals = self.data[bucket]
        if category:
            return round(vals.get(category, 0.0), 2)
        return round(sum(vals.values()), 2)

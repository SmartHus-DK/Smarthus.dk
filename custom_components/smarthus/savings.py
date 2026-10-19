"""Beregning og lagring af besparelser og solcelleoverblik."""

from __future__ import annotations

from datetime import datetime

from .const import SAVINGS_CATEGORIES
from .planner import Slot

# Spring urealistiske hop over (fx når en måler nulstilles) – kWh pr. minut
MAX_DELTA_KWH = 5.0

# Perioder for solcelleoverblikket
SOLAR_BUCKETS = ("today", "month", "year", "total")
SOLAR_FIELDS = ("produced", "self_kwh", "export_kwh", "self_value", "export_income", "import_kwh", "import_cost")
KEEP_DAYS = 35
KEEP_MONTHS = 25


def _zero_cat() -> dict:
    return {c: 0.0 for c in SAVINGS_CATEGORIES}


def _zero_solar() -> dict:
    return {f: 0.0 for f in SOLAR_FIELDS}


def empty() -> dict:
    return {
        "version": 2,
        "day": None, "month": None, "year": None,
        "today": _zero_cat(), "month_values": _zero_cat(), "total": _zero_cat(),
        "solar": {b: _zero_solar() for b in SOLAR_BUCKETS},
        "solar_days": {}, "solar_months": {},
        "last": {},
    }


def _migrate(data: dict) -> dict:
    """Ældre data: solceller talte med i SmartHus-besparelsen. Nu er de skilt ud."""
    if data.get("version", 1) >= 2:
        return data
    for bucket in ("today", "month_values", "total"):
        vals = data.get(bucket) or {}
        old_solar = vals.pop("solar", 0.0) or 0.0
        # Den gamle solcelleværdi kan ikke deles i egetforbrug/salg – den lægges under egetforbrug
        target = {"today": "today", "month_values": "month", "total": "total"}[bucket]
        data.setdefault("solar", {b: _zero_solar() for b in SOLAR_BUCKETS})
        data["solar"].setdefault(target, _zero_solar())
        data["solar"][target]["self_value"] += old_solar
        if target == "month":
            data["solar"].setdefault("year", _zero_solar())
            data["solar"]["year"]["self_value"] += old_solar
    data["version"] = 2
    return data


class SavingsCalculator:
    """Beregner besparelser ud fra energimålere.

    SmartHus-besparelse (det styringen sparer):
    * Elbil, varmepumpe og varmt vand: kWh × (dagens gennemsnitspris − aktuel pris)
      → hvad det sparer at flytte forbruget til billige timer.
    * Batteri: afladet kWh × aktuel pris − opladet kWh × aktuel pris.

    Solceller (vises for sig – de producerer også uden SmartHus):
    * Produceret kWh, egetforbrug × købspris (sparet) og eksport × salgspris (tjent).
    """

    def __init__(self, data: dict | None = None) -> None:
        self.data = _migrate(data) if data else empty()
        for key, val in empty().items():
            self.data.setdefault(key, val)
        for bucket in ("today", "month_values", "total"):
            for c in SAVINGS_CATEGORIES:
                self.data[bucket].setdefault(c, 0.0)
        for b in SOLAR_BUCKETS:
            self.data["solar"].setdefault(b, _zero_solar())
            for f in SOLAR_FIELDS:
                self.data["solar"][b].setdefault(f, 0.0)

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

    def _roll(self, now_local: datetime) -> None:
        day = now_local.date().isoformat()
        month = now_local.strftime("%Y-%m")
        year = now_local.strftime("%Y")
        if self.data["day"] != day:
            self.data["day"] = day
            self.data["today"] = _zero_cat()
            self.data["solar"]["today"] = _zero_solar()
        if self.data["month"] != month:
            self.data["month"] = month
            self.data["month_values"] = _zero_cat()
            self.data["solar"]["month"] = _zero_solar()
        if self.data.get("year") != year:
            # Første gang (ældre data uden år) beholdes måneden som start på året
            if self.data.get("year") is not None:
                self.data["solar"]["year"] = _zero_solar()
            self.data["year"] = year

    def update(self, now_local: datetime, slot: Slot | None, day_avg: float | None,
               readings: dict[str, float | None]) -> None:
        self._roll(now_local)
        deltas = {k: self._delta(k, v) for k, v in readings.items()}
        if slot is None:
            return
        price, sale = slot.price, slot.sale
        ref = day_avg if day_avg is not None else price

        # --- SmartHus-besparelse ---
        gained = _zero_cat()
        ev_kwh = sum(v for k, v in deltas.items() if k == "ev" or k.startswith("ev:"))
        gained["ev"] = ev_kwh * (ref - price)
        gained["heatpump"] = deltas.get("heatpump", 0.0) * (ref - price)
        gained["hotwater"] = deltas.get("hotwater", 0.0) * (ref - price)
        gained["battery"] = (deltas.get("battery_discharge", 0.0) - deltas.get("battery_charge", 0.0)) * price
        for c, v in gained.items():
            for bucket in ("today", "month_values", "total"):
                self.data[bucket][c] = round(self.data[bucket].get(c, 0.0) + v, 4)

        # --- Solceller og strøm købt fra nettet ---
        add: dict[str, float] = {}
        if "solar" in readings:
            solar = deltas.get("solar", 0.0)
            if readings.get("export") is not None:
                export = min(deltas.get("export", 0.0), solar)
            else:
                export = 0.0
            own = max(solar - export, 0.0)
            if solar:
                add.update({
                    "produced": solar,
                    "self_kwh": own,
                    "export_kwh": export,
                    "self_value": own * price,
                    "export_income": export * sale,
                })
        imported = deltas.get("import", 0.0)
        if imported:
            add.update({"import_kwh": imported, "import_cost": imported * price})
        if not add:
            return
        targets = [self.data["solar"][b] for b in SOLAR_BUCKETS]
        day_key, month_key = now_local.date().isoformat(), now_local.strftime("%Y-%m")
        targets.append(self.data["solar_days"].setdefault(day_key, _zero_solar()))
        targets.append(self.data["solar_months"].setdefault(month_key, _zero_solar()))
        for t in targets:
            for f, v in add.items():
                t[f] = round(t.get(f, 0.0) + v, 4)
        self._trim()

    def _trim(self) -> None:
        for key, keep in (("solar_days", KEEP_DAYS), ("solar_months", KEEP_MONTHS)):
            d = self.data[key]
            for old in sorted(d)[:-keep]:
                del d[old]

    # --- Aflæsning --------------------------------------------------------
    def value(self, bucket: str, category: str | None = None) -> float:
        vals = self.data[bucket]
        if category:
            return round(vals.get(category, 0.0), 2)
        return round(sum(vals.get(c, 0.0) for c in SAVINGS_CATEGORIES), 2)

    def solar(self, bucket: str, field: str | None = None) -> float:
        s = self.data["solar"][bucket]
        if field:
            return round(s.get(field, 0.0), 2)
        return round(s.get("self_value", 0.0) + s.get("export_income", 0.0), 2)

    def solar_attrs(self, bucket: str) -> dict:
        s = self.data["solar"][bucket]
        return {f: round(s.get(f, 0.0), 2) for f in SOLAR_FIELDS}

    def solar_history(self) -> dict:
        def rows(d: dict) -> list[dict]:
            return [{"period": k, **{f: round(v.get(f, 0.0), 2) for f in SOLAR_FIELDS}} for k, v in sorted(d.items())]
        return {"days": rows(self.data["solar_days"]), "months": rows(self.data["solar_months"])}


def import_price(s: dict) -> float | None:
    """Gennemsnitlig pris for den købte strøm (kr/kWh)."""
    kwh = s.get("import_kwh", 0.0)
    return round(s.get("import_cost", 0.0) / kwh, 3) if kwh > 0.05 else None

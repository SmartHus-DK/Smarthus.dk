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


# Batteriets handel: hvad strømmen i batteriet har kostet, og hvad salget gav
TRADE_FIELDS = ("sold_kwh", "income", "cost_grid", "cost_solar", "profit",
                "house_kwh", "house_value", "house_cost", "charged_grid_kwh", "charged_solar_kwh")
TRADE_BUCKETS = ("today", "month", "total")
ROUND_TRIP = 0.90  # ca. 10 % tab fra opladning til afladning


def _zero_trade() -> dict:
    return {f: 0.0 for f in TRADE_FIELDS}


def _empty_trade() -> dict:
    return {"stock_kwh": 0.0, "stock_grid": 0.0, "stock_solar": 0.0,
            **{b: _zero_trade() for b in TRADE_BUCKETS}}


def empty() -> dict:
    return {
        "version": 2,
        "day": None, "month": None, "year": None,
        "today": _zero_cat(), "month_values": _zero_cat(), "total": _zero_cat(),
        "solar": {b: _zero_solar() for b in SOLAR_BUCKETS},
        "solar_days": {}, "solar_months": {},
        "battery_trade": _empty_trade(),
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
        for key, val in _empty_trade().items():
            self.data["battery_trade"].setdefault(key, val)

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
            self.data["battery_trade"]["today"] = _zero_trade()
        if self.data["month"] != month:
            self.data["month"] = month
            self.data["month_values"] = _zero_cat()
            self.data["solar"]["month"] = _zero_solar()
            self.data["battery_trade"]["month"] = _zero_trade()
        if self.data.get("year") != year:
            # Første gang (ældre data uden år) beholdes måneden som start på året
            if self.data.get("year") is not None:
                self.data["solar"]["year"] = _zero_solar()
            self.data["year"] = year

    def update(self, now_local: datetime, slot: Slot | None, day_avg: float | None,
               readings: dict[str, float | None], battery_action: str | None = None,
               battery_kwh: float | None = None) -> None:
        self._roll(now_local)
        deltas = {k: self._delta(k, v) for k, v in readings.items()}
        if slot is None:
            return
        if "battery_charge" in readings or "battery_discharge" in readings:
            self._trade(slot, deltas, battery_action, battery_kwh, export_known=readings.get("export") is not None)
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

    def _trade(self, slot: Slot, deltas: dict, action: str | None, battery_kwh: float | None,
               export_known: bool) -> None:
        """Følg hvad strømmen i batteriet har kostet, og hvad den giver, når den bruges eller sælges.

        * Opladning i en planlagt netopladning koster købsprisen.
        * Anden opladning er solstrøm – den koster den salgspris, man gav afkald på.
        * Afladning i en salgsperiode sælges (højst det, der eksporteres) til salgsprisen.
          Resten bruges i huset og sparer købsprisen.
        * Omkostningen er gennemsnittet af strømmen i batteriet, inkl. ca. 10 % tab.
        """
        t = self.data["battery_trade"]
        price, sale = slot.price, slot.sale
        charge = deltas.get("battery_charge", 0.0)
        out = deltas.get("battery_discharge", 0.0)
        add = _zero_trade()
        if charge > 0:
            if action == "charge":
                t["stock_grid"] += charge * price
                add["charged_grid_kwh"] = charge
            else:
                t["stock_solar"] += charge * max(sale, 0.0)
                add["charged_solar_kwh"] = charge
            t["stock_kwh"] += charge
        if out > 0:
            used = min(out / ROUND_TRIP, t["stock_kwh"]) if t["stock_kwh"] > 0 else 0.0
            share = used / t["stock_kwh"] if t["stock_kwh"] > 0 else 0.0
            cost_g, cost_s = t["stock_grid"] * share, t["stock_solar"] * share
            if not used:  # ukendt oprindelse (fx lige efter start): regn med købsprisen nu
                cost_g = out / ROUND_TRIP * price
            t["stock_grid"] -= t["stock_grid"] * share
            t["stock_solar"] -= t["stock_solar"] * share
            t["stock_kwh"] -= used
            if action == "discharge":
                sold = min(out, deltas.get("export", 0.0)) if export_known else out
            else:
                sold = 0.0
            house = out - sold
            frac_sold = sold / out
            add["sold_kwh"], add["house_kwh"] = sold, house
            add["income"] = sold * sale
            add["house_value"] = house * price
            add["cost_grid"] = cost_g * frac_sold
            add["cost_solar"] = cost_s * frac_sold
            add["house_cost"] = (cost_g + cost_s) * (1 - frac_sold)
            add["profit"] = add["income"] - add["cost_grid"] - add["cost_solar"]
        # Hold lageret i takt med batteriets niveau (behold kr/kWh)
        if battery_kwh is not None and battery_kwh >= 0:
            if t["stock_kwh"] > 0.01:
                f = battery_kwh / t["stock_kwh"]
                t["stock_grid"] *= f
                t["stock_solar"] *= f
            t["stock_kwh"] = battery_kwh
        if any(add.values()):
            for b in TRADE_BUCKETS:
                for k, v in add.items():
                    t[b][k] = round(t[b].get(k, 0.0) + v, 4)

    def trade(self, bucket: str) -> dict:
        t = self.data["battery_trade"]
        out = {k: round(v, 2) for k, v in t[bucket].items()}
        out["avg_cost_in_battery"] = round((t["stock_grid"] + t["stock_solar"]) / t["stock_kwh"], 3) \
            if t["stock_kwh"] > 0.05 else None
        out["stock_kwh"] = round(t["stock_kwh"], 2)
        return out

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

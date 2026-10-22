"""Energioverblik: forbrug, køb og salg i kWh og kr pr. enhed (ingen Home Assistant-afhængigheder).

Målerne findes i Home Assistants Energi-dashboard (net, solceller, batteri og individuelle enheder)
eller – hvis det ikke er sat op – i SmartHus' egne valgte målere. Regnskabet bygges time for time
ud fra langtidsstatistikken og timens faktiske elpris.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta

KEEP_DAYS = 62

# Farver til enhederne (samme stil som resten af kortene)
DEVICE_COLORS = ("#e07b39", "#c9a227", "#3aa0c8", "#d1557a", "#5d9c59", "#a0785a", "#7b8794")
EV_COLOR = "#3E6FD8"  # samme blå som elbilen på de andre kort


def _ids(*vals) -> list[str]:
    out: list[str] = []
    for v in vals:
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    return out


def layout_from_energy_prefs(prefs: dict | None, names: dict[str, str] | None = None) -> dict | None:
    """Læs målerne fra Home Assistants Energi-dashboard.

    prefs: {"energy_sources": [...], "device_consumption": [...]} som i HA's energy-manager.
    names: visningsnavne for statistik-id'er (fx friendly_name), bruges når en enhed ikke har eget navn.
    """
    if not prefs:
        return None
    names = names or {}
    imp: list[str] = []
    exp: list[str] = []
    solar: list[str] = []
    bat_in: list[str] = []
    bat_out: list[str] = []
    for src in prefs.get("energy_sources") or []:
        t = src.get("type")
        if t == "grid":
            # Ældre format: flow_from/flow_to-lister. Nyere: målerne direkte på kilden.
            for f in src.get("flow_from") or []:
                imp += _ids(f.get("stat_energy_from"))
            for f in src.get("flow_to") or []:
                exp += _ids(f.get("stat_energy_to"))
            imp += _ids(src.get("stat_energy_from"))
            exp += _ids(src.get("stat_energy_to"))
        elif t == "solar":
            solar += _ids(src.get("stat_energy_from"))
        elif t == "battery":
            bat_out += _ids(src.get("stat_energy_from"))
            bat_in += _ids(src.get("stat_energy_to"))
    devices = []
    for i, d in enumerate(prefs.get("device_consumption") or []):
        stat = d.get("stat_consumption")
        if not stat or d.get("included_in_stat"):
            continue  # underenheder tælles allerede med i deres "forælder"
        name = d.get("name") or clean_name(names.get(stat) or stat)
        devices.append({"id": f"d{len(devices) + 1}", "name": name, "stat": stat, "kind": device_kind(name)})
    layout = {"source": "energy", "import": list(dict.fromkeys(imp)), "export": list(dict.fromkeys(exp)),
              "solar": solar, "battery_in": bat_in, "battery_out": bat_out, "devices": devices}
    return layout if any(layout[k] for k in ("import", "export", "solar", "battery_in", "devices")) else None


def layout_from_config(meters: dict[str, str | None], devices: list[tuple]) -> dict | None:
    """SmartHus' egne målere: import, export, solar, battery_in, battery_out + enheder [(navn, måler[, slags])]."""
    seen: set[str] = set()
    devs = []
    for name, stat, *kind in devices:
        if stat and stat not in seen:
            seen.add(stat)
            devs.append({"id": f"d{len(devs) + 1}", "name": name, "stat": stat, "kind": kind[0] if kind else device_kind(name)})
    layout = {"source": "smarthus", **{k: _ids(meters.get(k)) for k in ("import", "export", "solar", "battery_in", "battery_out")},
              "devices": devs}
    return layout if any(layout[k] for k in ("import", "export", "solar", "battery_in", "devices")) else None


def add_devices(layout: dict, extra: list[tuple[str, str | None, str]], known: set[str]) -> dict:
    """Tilføj SmartHus' egne enheder (fx elbilens lader), som ikke står i Energi-dashboardet.

    extra: [(navn, måler, slags)]. known: alle målere i Energi-dashboardet (også underenheder),
    så intet tælles to gange.
    """
    seen = set(known) | {d["stat"] for d in layout.get("devices") or []}
    kinds = {d.get("kind") for d in layout.get("devices") or []}
    for name, stat, kind in extra:
        if kind == "ev" and "ev" in kinds:
            continue  # Energi-dashboardet har allerede en lader – den samme strøm skal ikke tælles to gange
        if stat and stat not in seen:
            seen.add(stat)
            layout["devices"].append({"id": f"d{len(layout['devices']) + 1}", "name": name, "stat": stat, "kind": kind})
    return layout


_SUFFIX = re.compile(r"[\s\-·:]*\b(lifetime|total|accumulated|samlet)?\s*(energy|energi|forbrug|consumption)\s*$", re.I)


def clean_name(name: str) -> str:
    """'Enkehøj 25 - Easee Lifetime energy' → 'Enkehøj 25 - Easee'."""
    out = _SUFFIX.sub("", name or "").strip(" -·:")
    return out or name


def device_kind(name: str) -> str:
    n = (name or "").lower()
    if re.search(r"\b(ev|elbil|bil|lader|ladeboks|ladestander|charger)\b", n) or any(
            w in n for w in ("easee", "zaptec", "wallbox", "elbil", "ladeboks", "charger")):
        return "ev"
    if any(w in n for w in ("varmepumpe", "heat pump", "heatpump", "jordvarme", "luft til vand", "luft/vand")):
        return "heatpump"
    if "vand" in n or "water" in n or "vvb" in n:
        return "hotwater"
    return "device"


def layout_stats(layout: dict) -> set[str]:
    out = set()
    for k in ("import", "export", "solar", "battery_in", "battery_out"):
        out.update(layout.get(k) or [])
    out.update(d["stat"] for d in layout.get("devices") or [])
    return out


def layout_signature(layout: dict | None) -> str:
    if not layout:
        return ""
    parts = ["v2"] + [",".join(sorted(layout.get(k) or [])) for k in ("import", "export", "solar", "battery_in", "battery_out")]
    parts += [d["stat"] for d in layout.get("devices") or []]
    return "|".join(parts)


def hour_fields(layout: dict, kwh: dict[str, float], price: float | None, sale: float | None) -> dict[str, float]:
    """Én times regnskab. kwh: forbrug i timen pr. statistik-id."""
    s = lambda key: sum(max(kwh.get(i) or 0.0, 0.0) for i in layout.get(key) or [])  # noqa: E731
    p, sp = price or 0.0, sale or 0.0
    has_grid = bool(layout.get("import"))
    imp, exp, sol, b_in, b_out = s("import"), s("export"), s("solar"), s("battery_in"), s("battery_out")
    f: dict[str, float] = {}
    if layout.get("import") or layout.get("export"):
        f.update(grid_in=imp, grid_in_kr=imp * p, grid_out=exp, grid_out_kr=exp * sp)
    if layout.get("solar"):
        sol_exp = min(exp, sol)
        own = max(sol - sol_exp, 0.0)
        f.update(solar=sol, solar_self=own, solar_export=sol_exp, solar_kr=own * p + sol_exp * sp)
    if layout.get("battery_in") or layout.get("battery_out"):
        f.update(bat_in=b_in, bat_in_kr=b_in * p, bat_out=b_out, bat_out_kr=b_out * p)
    dev_sum = 0.0
    for d in layout.get("devices") or []:
        v = max(kwh.get(d["stat"]) or 0.0, 0.0)
        dev_sum += v
        f[d["id"]] = v
        f[d["id"] + "_kr"] = v * p
    if has_grid:
        house = max(imp + sol - exp + b_out - b_in, 0.0)
        f.update(house=house, house_kr=house * p)
        if layout.get("devices"):
            other = max(house - dev_sum, 0.0)
            f.update(other=other, other_kr=other * p)
        f["bill"] = imp * p - exp * sp
    if price is None:
        f["no_price"] = 1.0
    return f


def _add(dst: dict, src: dict) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0.0) + v


def _rnd(d: dict) -> dict:
    return {k: round(v, 4) for k, v in d.items()}


class EnergyBook:
    """Gemte dage og måneder + timer for i dag. Data er et almindeligt dict (gemmes i en Store)."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if isinstance(data, dict) else {}
        for k, v in (("sig", ""), ("layout", None), ("hours", {}), ("days", {}), ("months", {}),
                     ("updated", None), ("first", None)):
            self.data.setdefault(k, v)

    def apply(self, layout: dict, rows: list[dict], start: datetime, full: bool, now: datetime) -> None:
        """Erstat alt fra `start` (lokal tid, starten af en måned) med de nye timer.

        rows: [{"start": lokal datetime, "kwh": {stat: kWh}, "price": kr/kWh, "sale": kr/kWh}]
        """
        sig = layout_signature(layout)
        if full or self.data["sig"] != sig:
            self.data.update(hours={}, days={}, months={}, first=None)
        self.data["sig"], self.data["layout"] = sig, layout
        d0, m0 = start.date().isoformat(), start.strftime("%Y-%m")
        days = {k: v for k, v in self.data["days"].items() if k < d0}
        months = {k: v for k, v in self.data["months"].items() if k < m0}
        today = now.date().isoformat()
        hours: dict[str, dict] = {}
        for r in sorted(rows, key=lambda x: x["start"]):
            t = r["start"]
            if t < start:
                continue
            f = hour_fields(layout, r.get("kwh") or {}, r.get("price"), r.get("sale"))
            if not any(v for k, v in f.items() if k != "no_price"):
                continue
            d, m = t.date().isoformat(), t.strftime("%Y-%m")
            _add(days.setdefault(d, {}), f)
            _add(months.setdefault(m, {}), f)
            if d == today:
                hours[t.strftime("%H")] = _rnd(f)
            if not self.data["first"] or d < self.data["first"]:
                self.data["first"] = d
        self.data["days"] = {k: _rnd(v) for k, v in sorted(days.items())[-KEEP_DAYS:]}
        self.data["months"] = {k: _rnd(v) for k, v in sorted(months.items())}
        self.data["hours"] = {"date": today, **hours}
        self.data["updated"] = now.isoformat()

    # --- Aflæsning --------------------------------------------------------
    def overview(self, now: datetime) -> dict:
        """Perioder og søjler til kortet."""
        days, months = self.data["days"], self.data["months"]
        today = now.date()
        monday = today - timedelta(days=today.weekday())
        ym = now.strftime("%Y-%m")

        def total(items) -> dict:
            out: dict = {}
            for v in items:
                _add(out, v)
            return _rnd(out)

        hrs = self.data["hours"] if self.data["hours"].get("date") == today.isoformat() else {}
        week_days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
        month_days = [date(today.year, today.month, i).isoformat() for i in range(1, monthrange(today.year, today.month)[1] + 1)]
        years: dict[str, dict] = {}
        for k, v in months.items():
            _add(years.setdefault(k[:4], {}), v)
        series = {
            "today": [{"p": f"{h:02d}", **hrs.get(f"{h:02d}", {})} for h in range(24)],
            "week": [{"p": d, **days.get(d, {})} for d in week_days],
            "month": [{"p": d, **days.get(d, {})} for d in month_days],
            "total": [{"p": y, **_rnd(v)} for y, v in sorted(years.items())],
        }
        return {
            "periods": {
                "today": total([days.get(today.isoformat(), {})]),
                "week": total(days.get(d, {}) for d in week_days),
                "month": total([months.get(ym, {})]),
                "total": total(months.values()),
            },
            "series": series,
        }

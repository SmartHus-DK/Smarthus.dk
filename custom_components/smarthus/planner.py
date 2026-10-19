"""Ren planlægningslogik (ingen Home Assistant-afhængigheder, så den kan testes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo


@dataclass(frozen=True)
class Slot:
    """Et pristidsrum (typisk 15 minutter)."""

    start: datetime  # UTC
    end: datetime  # UTC
    spot: float  # kr/kWh, rå spotpris
    price: float  # kr/kWh, samlet købspris inkl. tariffer, afgifter og evt. moms
    sale: float  # kr/kWh, salgspris for eksport

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def slots_in(slots: list[Slot], start: datetime, end: datetime) -> list[Slot]:
    """Tidsrum der starter inden for [start, end)."""
    return [s for s in slots if start <= s.start < end]


def find_slot(slots: list[Slot], when: datetime) -> Slot | None:
    for s in slots:
        if s.start <= when < s.end:
            return s
    return None


def _count(slots: list[Slot], hours: float) -> int:
    if not slots or hours <= 0:
        return 0
    return max(1, round(hours / slots[0].hours))


def cheapest(slots: list[Slot], hours: float, *, contiguous: bool = False,
             most_expensive: bool = False) -> list[Slot]:
    """Vælg de billigste (eller dyreste) tidsrum svarende til `hours` timer."""
    n = _count(slots, hours)
    if n == 0:
        return []
    if n >= len(slots):
        return list(slots)
    sign = -1 if most_expensive else 1
    if not contiguous:
        chosen = sorted(slots, key=lambda s: (sign * s.price, s.start))[:n]
        return sorted(chosen, key=lambda s: s.start)
    ordered = sorted(slots, key=lambda s: s.start)
    best_i, best_val = 0, None
    window = sum(s.price for s in ordered[:n])
    for i in range(len(ordered) - n + 1):
        if i > 0:
            window += ordered[i + n - 1].price - ordered[i - 1].price
        # Kun ægte sammenhængende blokke (ingen huller i data)
        if ordered[i + n - 1].end - ordered[i].start > timedelta(hours=hours) + timedelta(minutes=1):
            continue
        val = sign * window
        if best_val is None or val < best_val:
            best_i, best_val = i, val
    return ordered[best_i:best_i + n]


def local_days(slots: list[Slot], tz: tzinfo) -> dict[str, list[Slot]]:
    """Grupper tidsrum pr. lokal dato."""
    days: dict[str, list[Slot]] = {}
    for s in slots:
        key = s.start.astimezone(tz).date().isoformat()
        days.setdefault(key, []).append(s)
    return days


def in_window(slots: list[Slot], tz: tzinfo, start_h: float, end_h: float) -> list[Slot]:
    """Tidsrum inden for et dagligt tidsvindue, fx 22–6 (går over midnat) eller 0–24 (hele døgnet)."""
    if end_h - start_h >= 24 or start_h == end_h:
        return list(slots)
    out = []
    for s in slots:
        lt = s.start.astimezone(tz)
        h = lt.hour + lt.minute / 60
        ok = start_h <= h < end_h if start_h < end_h else (h >= start_h or h < end_h)
        if ok:
            out.append(s)
    return out


def avg(slots: list[Slot]) -> float | None:
    return sum(s.price for s in slots) / len(slots) if slots else None


def merge_periods(slots: list[Slot], tz: tzinfo) -> list[str]:
    """Saml tidsrum til læsbare perioder, fx '02:00–05:00'."""
    out: list[str] = []
    cur_start = cur_end = None
    for s in sorted(slots, key=lambda x: x.start):
        if cur_end is not None and s.start == cur_end:
            cur_end = s.end
            continue
        if cur_start is not None:
            out.append(_fmt(cur_start, cur_end, tz))
        cur_start, cur_end = s.start, s.end
    if cur_start is not None:
        out.append(_fmt(cur_start, cur_end, tz))
    return out


def _fmt(a: datetime, b: datetime, tz: tzinfo) -> str:
    la, lb = a.astimezone(tz), b.astimezone(tz)
    day = la.strftime("%d/%m")
    return f"{day} {la:%H:%M}–{lb:%H:%M}"


EV_DEFAULTS = {
    "hours": 4.0,
    "ready_hour": 7.0,
    "target_soc": 80.0,
    "battery_capacity": 60.0,
    "charge_power": 11.0,
    "solar_min_a": 6.0,
    "solar_max_a": 16.0,
    "solar_start_kw": 4.0,
}


def car_settings(settings: dict, cid: str) -> dict:
    """Indstillinger for én bil. Nøglerne hedder fx 'ev_target_soc' eller 'ev_tesla_target_soc'."""
    return {k: float(settings.get(f"{cid}_{k}", settings.get(f"ev_{k}", v))) for k, v in EV_DEFAULTS.items()}


@dataclass
class CarInput:
    cid: str
    name: str = "Elbil"
    soc: float | None = None
    force: bool = False


@dataclass
class EvPlan:
    """Plan for én elbil."""

    cid: str
    name: str = "Elbil"
    slots: list[Slot] = field(default_factory=list)
    on: bool = False
    hours_needed: float | None = None
    target_reached: bool = False
    soc: float | None = None
    energy: float | None = None  # kWh fra nettet
    cost_now: float | None = None  # kr hvis der lades med det samme
    cost_planned: float | None = None  # kr i de planlagte billigste timer
    limited: bool = False  # plads blev begrænset af maks. samlet ladeeffekt


@dataclass
class Plan:
    """Resultat af én planlægning."""

    current: Slot | None = None
    day_avg: float | None = None
    battery_charge: list[Slot] = field(default_factory=list)
    battery_discharge: list[Slot] = field(default_factory=list)
    battery_action: str = "normal"
    solar_curtail: bool = False
    evs: dict[str, EvPlan] = field(default_factory=dict)
    hotwater_slots: list[Slot] = field(default_factory=list)
    hotwater_on: bool = False
    heating_boost: list[Slot] = field(default_factory=list)
    heating_reduce: list[Slot] = field(default_factory=list)
    heating_mode: str = "normal"
    cheap_now: bool = False
    expensive_now: bool = False

    # Genveje til den første bil (bagudkompatibelt)
    def _first(self) -> EvPlan:
        return next(iter(self.evs.values()), EvPlan("ev"))

    @property
    def ev_slots(self) -> list[Slot]:
        return self._first().slots

    @property
    def ev_on(self) -> bool:
        return self._first().on

    @property
    def ev_hours_needed(self) -> float | None:
        return self._first().hours_needed

    @property
    def ev_target_reached(self) -> bool:
        return self._first().target_reached

    @property
    def ev_energy(self) -> float | None:
        return self._first().energy

    @property
    def ev_cost_now(self) -> float | None:
        return self._first().cost_now

    @property
    def ev_cost_planned(self) -> float | None:
        return self._first().cost_planned


def _percentile(values: list[float], q: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    k = (len(vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def build_plan(slots: list[Slot], now: datetime, tz: tzinfo, settings: dict,
               soc: float | None = None, ev_force: bool = False,
               ev_soc: float | None = None, cars: list[CarInput] | None = None) -> Plan:
    """Byg den samlede plan for alle moduler ud fra priserne."""
    plan = Plan()
    if cars is None:
        cars = [CarInput("ev", "Elbil", soc=ev_soc, force=ev_force)]
    if not slots:
        plan.evs = {c.cid: EvPlan(c.cid, c.name) for c in cars}
        return plan
    plan.current = find_slot(slots, now)
    days = local_days(slots, tz)
    today_key = now.astimezone(tz).date().isoformat()
    today = days.get(today_key, [])
    plan.day_avg = avg(today)

    if plan.current and today:
        prices = [s.price for s in today]
        plan.cheap_now = plan.current.price <= _percentile(prices, 0.25)
        plan.expensive_now = plan.current.price >= _percentile(prices, 0.75)

    # Daglige planer (i dag + i morgen hvis kendt)
    for key in sorted(days):
        if key < today_key:
            continue
        d = days[key]
        g = lambda k, dflt: float(settings.get(k, dflt))  # noqa: E731
        win = lambda name: in_window(d, tz, g(f"{name}_from", 0), g(f"{name}_to", 24))  # noqa: E731
        # Batteri: lad billigst, aflad dyrest – kun hvis prisforskellen er stor nok
        ch = cheapest(win("battery_charge"), settings["battery_charge_hours"])
        dis_pool = [s for s in win("battery_discharge") if s not in ch]
        dis = cheapest(dis_pool, settings["battery_discharge_hours"], most_expensive=True)
        if ch and dis and (avg(dis) or 0) - (avg(ch) or 0) >= settings["battery_min_spread"]:
            plan.battery_charge += ch
            plan.battery_discharge += dis
        # Varmt vand: sammenhængende billigste blok
        plan.hotwater_slots += cheapest(win("hotwater"), settings["hotwater_hours"], contiguous=True)
        # Varme: hæv i billigste timer, sænk i dyreste timer
        boost = cheapest(win("heating_boost"), settings["heating_boost_hours"])
        plan.heating_boost += boost
        if settings["heating_reduce_degrees"] > 0:
            pool = [s for s in win("heating_reduce") if s not in boost]
            plan.heating_reduce += cheapest(pool, settings["heating_reduce_hours"], most_expensive=True)

    plan.evs = plan_cars(slots, now, tz, settings, cars)

    cur = plan.current
    if cur:
        # Eksport stoppes, når salgsprisen er under grænsen, og starter selv igen over den
        export_min = float(settings.get("solar_export_min_price", 0.0))
        if cur in plan.battery_charge and (soc is None or soc < settings["battery_max_soc"]):
            plan.battery_action = "charge"
        elif cur in plan.battery_discharge and (soc is None or soc > settings["battery_min_soc"]):
            plan.battery_action = "discharge"
        elif cur.sale < export_min and (soc is None or soc < settings["battery_max_soc"]):
            plan.battery_action = "charge"  # for lav salgspris: gem strømmen selv
        plan.solar_curtail = cur.sale < export_min
        plan.hotwater_on = cur in plan.hotwater_slots
        if cur in plan.heating_boost:
            plan.heating_mode = "boost"
        elif cur in plan.heating_reduce:
            plan.heating_mode = "reduce"
    return plan


def _next_ready(now: datetime, tz: tzinfo, ready_hour: int) -> datetime:
    local_now = now.astimezone(tz)
    ready = local_now.replace(hour=ready_hour % 24, minute=0, second=0, microsecond=0)
    if local_now >= ready:
        ready += timedelta(days=1)
    return ready


def plan_cars(slots: list[Slot], now: datetime, tz: tzinfo, settings: dict,
              cars: list[CarInput]) -> dict[str, EvPlan]:
    """Planlæg alle elbiler. Med maks. samlet ladeeffekt får bilen med det
    tidligste klar-tidspunkt førsteret til de billigste timer."""
    max_kw = float(settings.get("ev_max_total_power", 0) or 0)
    used: dict[datetime, float] = {}
    order = sorted(cars, key=lambda c: _next_ready(now, tz, int(car_settings(settings, c.cid)["ready_hour"])))
    result: dict[str, EvPlan] = {}
    for car in order:
        cs = car_settings(settings, car.cid)
        kw = max(cs["charge_power"], 0.1)
        ep = EvPlan(car.cid, car.name, soc=car.soc)
        available = None
        if max_kw > 0:
            available = lambda s, kw=kw: used.get(s.start, 0.0) + kw <= max_kw + 1e-6  # noqa: E731
        if car.soc is None:
            ep.slots = ev_plan(slots, now, tz, cs["hours"], int(cs["ready_hour"]), available=available)
            wanted = cs["hours"]
        else:
            ep.hours_needed = ev_hours_needed(car.soc, cs)
            ep.target_reached = ep.hours_needed <= 0
            wanted = ep.hours_needed
            if not ep.target_reached:
                ep.slots = ev_plan(slots, now, tz, ep.hours_needed, int(cs["ready_hour"]),
                                   from_now=True, available=available)
        if ep.slots and wanted > 0:
            planned_h = sum(s.hours for s in ep.slots)
            ep.limited = available is not None and planned_h + 0.01 < wanted - 0.25
        for s in ep.slots:
            used[s.start] = used.get(s.start, 0.0) + kw
        _ev_costs(ep, slots, now, cs)
        ep.on = (car.force or any(s.start <= now < s.end for s in ep.slots)) and not ep.target_reached
        result[car.cid] = ep
    # Bevar bilernes oprindelige rækkefølge
    return {c.cid: result[c.cid] for c in cars}


def charge_cost(seq: list[Slot], energy: float, kw: float) -> float | None:
    """Pris for at lade `energy` kWh ved `kw` gennem tidsrummene i rækkefølge."""
    remaining, total = energy, 0.0
    for s in seq:
        if remaining <= 1e-6:
            break
        e = min(remaining, kw * s.hours)
        total += e * s.price
        remaining -= e
    return None if remaining > 1e-3 else round(total, 2)


def _ev_costs(ep: EvPlan, slots: list[Slot], now: datetime, cs: dict) -> None:
    kw = max(cs["charge_power"], 0.1)
    if ep.hours_needed is not None:
        missing = max(0.0, cs["target_soc"] - (ep.soc or 0)) / 100
        energy = missing * cs["battery_capacity"] / CHARGE_EFFICIENCY
    else:
        energy = cs["hours"] * kw
    ep.energy = round(energy, 2)
    if energy <= 0:
        ep.cost_now = ep.cost_planned = 0.0
        return
    future = sorted((s for s in slots if s.end > now), key=lambda s: s.start)
    ep.cost_now = charge_cost(future, energy, kw)
    real = [find_slot(slots, s.start) for s in ep.slots if s.end > now]
    if real and all(real):
        ep.cost_planned = charge_cost(sorted(real, key=lambda s: s.start), energy, kw)
        if ep.cost_planned is None:
            # Planen dækker lidt mindre pga. afrunding til 15 min – brug gennemsnitsprisen
            avg_p = sum(s.price for s in real) / len(real)
            ep.cost_planned = round(energy * avg_p, 2)


CHARGE_EFFICIENCY = 0.9  # ladetab i lader og bil


def ev_hours_needed(ev_soc: float, cs: dict) -> float:
    """Timer der skal til for at nå mål-SoC (0 hvis nået)."""
    missing = max(0.0, cs["target_soc"] - ev_soc) / 100
    kw = max(cs["charge_power"], 0.1)
    return round(missing * cs["battery_capacity"] / kw / CHARGE_EFFICIENCY, 2)


def ev_plan(slots: list[Slot], now: datetime, tz: tzinfo, hours: float, ready_hour: int,
            from_now: bool = False, available=None) -> list[Slot]:
    """Billigste tidsrum i vinduet [klar-tid − 24 t, klar-tid).

    Hvis priserne for hele vinduet endnu ikke er kendt (før ca. kl. 13), ventes
    der – medmindre klar-tidspunktet nærmer sig, så lades der med det samme.
    `available` kan udelukke tidsrum (fx når en anden bil allerede bruger effekten).
    """
    if hours <= 0 or not slots:
        return []
    local_now = now.astimezone(tz)
    ready = _next_ready(now, tz, ready_hour)
    start = ready - timedelta(days=1)
    if from_now:
        window = [s for s in slots if s.end > now and s.start < ready]
        covered = bool(window) and max(s.end for s in window) >= ready
    else:
        window = slots_in(slots, start, ready)
        covered = bool(window) and max(s.end for s in window) >= ready and min(s.start for s in window) <= start
    if covered:
        pool = [s for s in window if available is None or available(s)]
        return cheapest(pool, hours)
    # Sikkerhed: data mangler – lad i de sidste timer før klar-tid
    if ready - local_now <= timedelta(hours=hours + 1):
        res = window[0].hours if window else 0.25
        out = []
        t = now
        while t < ready:
            out.append(Slot(t, t + timedelta(hours=res), 0, 0, 0))
            t += timedelta(hours=res)
        return out
    return []

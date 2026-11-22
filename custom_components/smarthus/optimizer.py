"""Samlet batteriplan: én beregning kvarter for kvarter (ingen Home Assistant-afhængigheder).

I stedet for flere regler, der hver især giver mening (billigste ladetimer, nat til solopgang, gem til de
dyre timer, ekstra opladning midt på dagen …), regnes alle muligheder igennem for de næste døgn: i hvert
kvarter kan batteriet

* "use"    – normal drift: solens overskud lader, batteriet dækker husets underskud
* "hold"   – gem: solens overskud lader stadig, men huset køber sit underskud fra nettet
* "charge" – lad fra nettet (med op til den indtastede ladeeffekt), solens overskud lader med

Den billigste plan samlet set vælges (dynamisk programmering over batteriets niveau), med:

* husets forventede forbrug og solprognosen time for time
* ladetab (ind og ud – ca. 10 % samlet)
* min./maks. niveau og ladeeffekt
* "min. prisforskel": hver kWh fra nettet skal mindst tjene så meget – ellers købes der hellere direkte
* "ekstra buffer" (add_buffer): bufferens kWh lades altid oven i næste opladning fra nettet
* en lille pris for at skifte, så batteriet ikke slår til og fra for småbeløb
* strøm, der er tilbage til sidst, er højst værd, hvad den koster at lade op igen (den skal ikke hamstres)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

ROUND_TRIP = 0.90
ETA = math.sqrt(ROUND_TRIP)          # tab hver vej (ind og ud)
SWITCH_COST = 0.02                   # kr pr. skift mellem tilstande – undgår at skifte for småbeløb
HOLD_TIE = 0.001                     # lille pris for at gemme – ved lige gode planer bruges batteriet
LEVELS = 60                          # trin mellem min. og maks. niveau
ACTIONS = ("use", "hold", "charge")


@dataclass
class OptSlot:
    start: datetime
    end: datetime
    price: float        # købspris kr/kWh
    sale: float         # salgspris kr/kWh
    load: float         # husets forventede forbrug i kvarteret (kWh)
    solar: float        # solens forventede produktion i kvarteret (kWh)
    can_charge: bool = True
    force_charge: bool = False   # skal lade fra nettet (ekstra buffer) – resten af planen regnes om omkring det


@dataclass
class OptResult:
    actions: list[str]                     # pr. kvarter
    levels: list[float]                    # batteriets niveau (kWh) ved starten af hvert kvarter + til sidst
    cost: float
    grid_kwh: list[float] = field(default_factory=list)   # kWh fra nettet til batteriet pr. kvarter


def _step(a: str, e: float, s: OptSlot, floor: float, full: float, max_c: float, max_d: float):
    """Ét kvarter: (nyt niveau, købt kWh, solgt kWh, kWh fra nettet til batteriet)."""
    net = s.solar - s.load
    imp = exp = grid_in = 0.0
    room = max(full - e, 0.0)
    if a == "charge":
        sun_in = max(net, 0.0) * ETA
        gain = min(room, max_c + sun_in)
        grid_in = max(gain / ETA - max(net, 0.0), 0.0)
        bal = s.load - s.solar + gain / ETA          # positiv: købes, negativ: sælges
        imp, exp = (bal, 0.0) if bal > 0 else (0.0, -bal)
        return e + gain, imp, exp, grid_in
    if net >= 0:                                     # solen giver overskud: lader batteriet (begge tilstande)
        into = min(net * ETA, room, max_c + net * ETA)
        exp = net - into / ETA
        return e + into, 0.0, exp, 0.0
    if a == "hold":
        return e, -net, 0.0, 0.0
    give = min(max(e - floor, 0.0) * ETA, -net, max_d)
    return e - give / ETA, -net - give, 0.0, 0.0


def _limits(s: OptSlot, charge_kw: float, span: float) -> tuple[float, float]:
    hrs = max((s.end - s.start).total_seconds() / 3600, 0.0)
    if charge_kw > 0:
        return charge_kw * hrs, max(charge_kw * 1.5, span * 0.5) * hrs
    return span * 0.5 * hrs, span * hrs


def optimize(slots: list[OptSlot], e0: float, *, floor: float, full: float, charge_kw: float,
             min_spread: float = 0.0, can_hold: bool = True, prev_action: str | None = None,
             end_value: float | None = None) -> OptResult | None:
    """Billigste plan for batteriet over `slots` (sammenhængende kvarterer fra nu).

    Værdien af hvert niveau regnes baglæns på et gitter; niveauer mellem gitterpunkterne interpoleres, så
    også små ændringer (et kvarters forbrug) tæller med.
    """
    if not slots or full <= floor + 0.05:
        return None
    n = LEVELS
    span = full - floor
    step = span / n
    grid = [floor + i * step for i in range(n + 1)]
    acts = ACTIONS if can_hold else ("use", "charge")
    na = len(acts)
    if end_value is None:
        # Strøm, der er tilbage til sidst, er højst værd, hvad den koster at lade op igen (den billigste pris) –
        # så hamstres den ikke, og batteriet bruges hellere i de dyre timer undervejs
        end_value = min(s.price for s in slots) * ETA

    def interp(Vt, e, ai):
        if e < floor:
            # Under den nedre grænse (fx lidt eget forbrug om natten): det, der mangler op til grænsen, koster
            # det samme som den første kWh over den. Uden det regnes et batteri under grænsen som stående på
            # grænsen, og så ser "lad senere" altid lidt bedre ud end "lad nu" – opladningen udskydes igen og igen
            return Vt[0][ai] + (Vt[0][ai] - Vt[1][ai]) * (floor - e) / step
        x = (min(e, full) - floor) / step
        k = min(int(x), n - 1)
        f = x - k
        return Vt[k][ai] * (1 - f) + Vt[k + 1][ai] * f

    def options(t, e, Vnext):
        s = slots[t]
        max_c, max_d = _limits(s, charge_kw, span)
        out = []
        for ai, a in enumerate(acts):
            if a == "charge" and (not s.can_charge or e >= full - 1e-6):
                out.append(None)
                continue
            if a != "charge" and s.force_charge and s.can_charge and e < full - 1e-6:
                out.append(None)
                continue
            e2, imp, exp, g = _step(a, e, s, floor, full, max_c, max_d)
            tie = HOLD_TIE if a == "hold" else 0.0       # lige godt: brug hellere end gem
            out.append((imp * s.price - exp * s.sale + g * min_spread + tie + interp(Vnext, e2, ai), e2, g))
        return out

    T = len(slots)
    Vs = [None] * (T + 1)
    Vs[T] = [[-(grid[i] - floor) * end_value] * na for i in range(n + 1)]
    for t in range(T - 1, -1, -1):
        Vt = []
        for i in range(n + 1):
            opts = options(t, grid[i], Vs[t + 1])
            row = []
            for pi in range(na):
                best = None
                for ai, o in enumerate(opts):
                    if o is None:
                        continue
                    tot = o[0] + (SWITCH_COST if ai != pi else 0.0)
                    if best is None or tot < best:
                        best = tot
                row.append(best if best is not None else 0.0)
            Vt.append(row)
        Vs[t] = Vt
    pi = acts.index(prev_action) if prev_action in acts else 0
    e = e0
    actions, levels, grid_kwh = [], [e0], []
    cost = 0.0
    for t in range(T):
        opts = options(t, e, Vs[t + 1])
        best, bi = None, 0
        for ai, o in enumerate(opts):
            if o is None:
                continue
            tot = o[0] + (SWITCH_COST if ai != pi else 0.0)
            if best is None or tot < best - 1e-9:
                best, bi = tot, ai
        if t == 0:
            cost = best or 0.0
        _, e, g = opts[bi]
        actions.append(acts[bi])
        grid_kwh.append(g)
        levels.append(e)
        pi = bi
    return OptResult(actions, levels, cost, grid_kwh)


def morning_buffer(slots: list[OptSlot], tz: tzinfo, buffer_kwh: float, first: int = 5, last: int = 9) -> None:
    """Læg "ekstra buffer" på næste morgens forventede forbrug (kl. 5–9), så nattens opladning tager lidt mere med."""
    if buffer_kwh <= 0:
        return
    morning = []
    lt0 = slots[0].start.astimezone(tz) if slots else None
    skipping = lt0 is not None and first <= lt0.hour < last and (lt0.hour, lt0.minute) != (first, 0)
    for s in slots:
        h = s.start.astimezone(tz).hour
        if first <= h < last:
            if skipping:
                continue                              # morgenen er allerede i gang: bufferen er til næste morgen
            morning.append(s)
        else:
            skipping = False
            if morning:
                break                                 # kun den første hele morgen
    if not morning:
        return
    add = buffer_kwh / len(morning)
    for s in morning:
        s.load += add


def add_buffer(slots: list[OptSlot], res: OptResult, e0: float, *, floor: float, full: float, charge_kw: float,
               buffer_kwh: float, keep: tuple[datetime, float] | None = None,
               window_h: float = 8.0) -> tuple[OptResult, tuple[datetime, float] | None]:
    """"Ekstra buffer ved netopladning": lad bufferens kWh ekstra oven i den næste planlagte opladning fra
    nettet – uanset prisforskellen – i de billigste ekstra kvarterer op til opladningens slutning (højst fuldt).

    `keep` er sidste beregnings løfte (slutning, niveau), så bufferen også bliver ladet, når beregningen undervejs
    selv mener, den er færdig. Returnerer den nye plan og løftet (eller None)."""
    if buffer_kwh <= 0 or not slots or charge_kw <= 0:
        return res, None
    now = slots[0].start
    acts = list(res.actions)
    lv = res.levels
    span = full - floor
    j = desired = end = None
    promised = False                                      # slutningen er givet af sidste beregnings løfte
    i = 0
    while i < len(acts):                                   # første opladning (perioder adskilt kun af "gem" er én)
        if acts[i] != "charge":
            i += 1
            continue
        j = i
        while True:
            while j + 1 < len(acts) and acts[j + 1] == "charge":
                j += 1
            k = j + 1
            while k < len(acts) and acts[k] == "hold":
                k += 1
            if k < len(acts) and k > j + 1 and acts[k] == "charge":
                j = k
                continue
            break
        end = slots[j].end
        desired = min(lv[j + 1] + buffer_kwh, full)
        break
    if keep is not None and keep[0] > now:
        if end is None or keep[0] >= end:
            jj = max((n for n, s in enumerate(slots) if s.end <= keep[0]), default=None)
            if jj is not None:
                j, end = jj, slots[jj].end
                desired = max(desired or 0.0, min(keep[1], full))
                promised = True
        else:
            desired = max(desired, min(keep[1], full))
    if j is None or desired is None:
        return res, None

    def simulate(actions):
        e, out, g = e0, [e0], []
        for s, a in zip(slots, actions):
            max_c, max_d = _limits(s, charge_kw, span)
            e, _, _, gi = _step(a, e, s, floor, full, max_c, max_d)
            out.append(e)
            g.append(gi)
        return out, g

    levels, grid = simulate(acts)
    # Bufferen må også lades lige efter opladningen (fx en lidt billigere time kl. 4–5), så længe batteriet
    # ikke bruges imellem – højst `window_h` / 2 timer efter
    j2 = j
    while (not promised and j2 + 1 < len(acts) and slots[j2 + 1].start < end + timedelta(hours=window_h / 2)
           and not (acts[j2 + 1] == "use" and levels[j2 + 2] < levels[j2 + 1] - 1e-6)):
        j2 += 1
    if j2 != j:
        j, end = j2, slots[j2].end
    lo = next((n for n, s in enumerate(slots) if (end - s.start).total_seconds() <= window_h * 3600), 0)
    first = next((n for n in range(j + 1) if acts[n] == "charge"), j)
    last_use = max((n for n in range(lo, first) if acts[n] == "use" and lv[n + 1] < lv[n] - 1e-6), default=lo - 1)
    lo = max(lo, last_use + 1)                       # ikke før batteriet bruges igen – så går det ekstra tabt
    cand = sorted((n for n in range(lo, j + 1) if acts[n] != "charge" and slots[n].can_charge),
                  key=lambda n: (slots[n].price, n))
    for n in cand:
        if levels[j + 1] >= desired - 0.02:
            break
        acts[n] = "charge"
        levels, grid = simulate(acts)
    if acts == res.actions:
        return res, (end, desired)
    return OptResult(acts, levels, res.cost, grid), (end, desired)

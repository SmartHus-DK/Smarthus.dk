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
BASE_HOURS = range(0, 6)  # nattens timer: det laveste af dem er husets grundforbrug
MIN_DAY_KWH = 0.2    # døgn med mindre forbrug end dette har ingen data (måleren fandtes ikke endnu) – tæller ikke med
SUN_START, SUN_END = 6, 21  # timer hvor solceller regnes at producere (lokal tid)
EFFICIENCY = 0.95  # tab ved afladning
DEFAULT_KW = 0.5     # standardforbrug, indtil mønsteret er lært
EVENING_KW = 1.5
EVENING_HOURS = range(17, 20)  # kl. 17–20

# Varmebehov: forbruget stiger, når det er koldt (varmepumpe, elvarme). Graddage pr. time under BASE_TEMP.
TEMP_LEARN_DAYS = 28   # længere historik til varmebehovet – temperaturen er regnet ud, så det følger stadig sæsonen
BASE_TEMP = 16.0       # °C – over denne udetemperatur regnes der ikke med opvarmning
MIN_K = 0.01           # kWh pr. grad pr. time – mindre end dette: huset varmes ikke med el
MAX_K = 1.0
MIN_T_STAT = 3.0       # sammenhængen skal være tydelig (ellers bruges mønsteret uden justering)
MIN_TEMP_SD = 1.0      # °C – temperaturen skal have varieret nok inden for samme time på døgnet
MIN_TEMP_HOURS = 72
MIN_ADJUST_SHARE = 0.3  # en mild dag kan højst trække timens forbrug ned til 30 % af mønsteret


@dataclass
class Profile:
    weekday: list[float] = field(default_factory=lambda: [0.0] * 24)
    weekend: list[float] = field(default_factory=lambda: [0.0] * 24)
    days: int = 0
    weekday_days: int = 0
    weekend_days: int = 0
    # Temperaturjustering: ekstra (eller mindre) forbrug pr. (dato, time) i forhold til mønsterets
    # gennemsnit, ud fra vejrudsigten og husets lærte varmebehov. Tom = ingen justering.
    adjust: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.days >= MIN_DAYS

    def hours_for(self, day: datetime) -> list[float]:
        base = self.weekend if day.weekday() >= 5 else self.weekday
        if not self.adjust:
            return base
        d = day.date()
        return [max(v + self.adjust.get((d, h), 0.0), v * MIN_ADJUST_SHARE) for h, v in enumerate(base)]

    def as_dict(self) -> dict:
        return {"weekday": [round(v, 3) for v in self.weekday],
                "weekend": [round(v, 3) for v in self.weekend], "days": self.days,
                "weekday_days": self.weekday_days, "weekend_days": self.weekend_days}


def default_profile() -> Profile:
    """Standardforbrug, indtil husets eget mønster er lært – så der gemmes lidt strøm til aftenens dyre timer."""
    hours = [EVENING_KW if h in EVENING_HOURS else DEFAULT_KW for h in range(24)]
    return Profile(weekday=list(hours), weekend=list(hours), days=MIN_DAYS)


def build_profile(hours: list[tuple[datetime, float]]) -> Profile:
    """Byg profilen ud fra (lokal timestart, kWh i timen)."""
    sums = {"weekday": [0.0] * 24, "weekend": [0.0] * 24}
    counts = {"weekday": [0] * 24, "weekend": [0] * 24}
    ok = [(s, k) for s, k in hours if k is not None and 0 <= k <= MAX_HOUR_KWH]
    # Timer før måleren begyndte at tælle (fx første døgn efter opsætning) har heller ingen data
    started = [s for s, k in hours if k is not None and k > 0]
    first = min(started) if started else None
    ok = [(s, k) for s, k in ok if first is None or s >= first]
    # Døgn uden forbrug har ingen data (fx før måleren blev sat op) – de ville trække gennemsnittet ned
    per_day: dict = {}
    for s, k in ok:
        per_day[s.date()] = per_day.get(s.date(), 0.0) + k
    real = {d for d, total in per_day.items() if total >= MIN_DAY_KWH}
    valid = 0
    for start, kwh in ok:
        if start.date() not in real:
            continue
        kind = "weekend" if start.weekday() >= 5 else "weekday"
        sums[kind][start.hour] += kwh
        counts[kind][start.hour] += 1
        valid += 1
    p = Profile(days=valid // 24,  # hele døgn med data
                weekday_days=sum(1 for d in real if d.weekday() < 5), weekend_days=sum(1 for d in real if d.weekday() >= 5))
    all_avg = [
        (sums["weekday"][h] + sums["weekend"][h]) / max(counts["weekday"][h] + counts["weekend"][h], 1)
        for h in range(24)
    ]
    for kind in ("weekday", "weekend"):
        vals = [sums[kind][h] / counts[kind][h] if counts[kind][h] else all_avg[h] for h in range(24)]
        # Huset bruger aldrig mindre end sit grundforbrug. Nogle målere viser for lidt (eller minus),
        # når solen står op – så ville solen se ud til at dække huset, længe før den gør det.
        base = min(vals[h] for h in BASE_HOURS)
        setattr(p, kind, [max(v, base) for v in vals])
    return p


def hdd(temp: float, base: float = BASE_TEMP) -> float:
    """Graddage for én time: hvor mange grader koldere end basistemperaturen."""
    return max(base - temp, 0.0)


@dataclass
class TempModel:
    """Husets varmebehov: k kWh ekstra pr. grad under BASE_TEMP pr. time."""
    k: float = 0.0
    active: bool = False
    hours: int = 0
    t_stat: float = 0.0
    temp_sd: float = 0.0
    typical_hdd: dict = field(default_factory=dict)   # (weekend?, time) -> gennemsnitlige graddage i mønsterets periode
    reason: str = ""

    def as_dict(self) -> dict:
        return {"k": round(self.k, 3), "active": self.active, "hours": self.hours, "t_stat": round(self.t_stat, 1),
                "temp_sd": round(self.temp_sd, 1), "base_temp": BASE_TEMP, "reason": self.reason}


def fit_temperature(hours: list[tuple[datetime, float]], temps: dict[datetime, float],
                    profile_from: datetime | None = None) -> TempModel:
    """Lær hvor meget forbruget stiger pr. grad koldere – ud fra (lokal timestart, kWh) og udetemperatur pr. time.

    Hver time på døgnet (hverdag/weekend for sig) har sit eget niveau, så kun temperaturens udsving
    inden for samme time tæller – aftensmaden kl. 18 forveksles ikke med kulde.
    `profile_from`: starten af mønsterets periode (de seneste 14 dage) – til det typiske varmebehov."""
    rows = [(t, k, hdd(temps[t])) for t, k in hours if k is not None and 0 <= k <= MAX_HOUR_KWH and t in temps]
    m = TempModel(hours=len(rows))
    if len(rows) < MIN_TEMP_HOURS:
        m.reason = "for få timer med både forbrug og temperatur"
        return m
    groups: dict = {}
    for t, k, x in rows:
        groups.setdefault((t.weekday() >= 5, t.hour), []).append((k, x))
    sxy = sxx = 0.0
    for vals in groups.values():
        if len(vals) < 2:
            continue
        my = sum(v[0] for v in vals) / len(vals)
        mx = sum(v[1] for v in vals) / len(vals)
        for y, x in vals:
            sxy += (x - mx) * (y - my)
            sxx += (x - mx) ** 2
    n = sum(len(v) for v in groups.values() if len(v) >= 2)
    m.temp_sd = (sxx / n) ** 0.5 if n else 0.0
    if sxx <= 0 or m.temp_sd < MIN_TEMP_SD:
        m.reason = "temperaturen har ikke varieret nok endnu"
        return m
    k = sxy / sxx
    # Usikkerhed: restspredning omkring linjen
    sse = 0.0
    for vals in groups.values():
        if len(vals) < 2:
            continue
        my = sum(v[0] for v in vals) / len(vals)
        mx = sum(v[1] for v in vals) / len(vals)
        sse += sum((y - my - k * (x - mx)) ** 2 for y, x in vals)
    dof = max(n - len(groups) - 1, 1)
    se = (sse / dof / sxx) ** 0.5 if sse > 0 else 1e-9
    m.k, m.t_stat = min(max(k, 0.0), MAX_K), k / se
    if k < MIN_K:
        m.reason = "forbruget afhænger ikke af udetemperaturen"
    elif m.t_stat < MIN_T_STAT:
        m.reason = "sammenhængen er ikke tydelig nok endnu"
    else:
        m.active = True
    recent = [(t, x) for t, _, x in rows if profile_from is None or t >= profile_from]
    acc: dict = {}
    for t, x in recent:
        acc.setdefault((t.weekday() >= 5, t.hour), []).append(x)
    m.typical_hdd = {g: sum(v) / len(v) for g, v in acc.items()}
    return m


def temperature_adjust(model: TempModel, forecast: dict[datetime, float]) -> dict:
    """Ekstra forbrug pr. (dato, time) ud fra vejrudsigten: k × (graddage − mønsterets typiske graddage)."""
    if not model.active:
        return {}
    out = {}
    for t, temp in forecast.items():
        typ = model.typical_hdd.get((t.weekday() >= 5, t.hour))
        if typ is None:
            continue
        out[(t.date(), t.hour)] = model.k * (hdd(temp) - typ)
    return out


# --- Varmepumpe med egen måler: døgnets forbrug ud fra udetemperaturen, fordelt efter timerne og SmartHus' plan ---
HP_HALF_LIFE_DAYS = 10.0   # nyere døgn vejer mest – så modellen følger med sæsonen og varmepumpens virkningsgrad
HP_MIN_DAYS = 3
HP_REDUCE_KEEP = 0.5       # i sparetimerne bruger varmepumpen ca. halvdelen – resten flyttes til ekstra-timerne


def hour_averages(hours: list[tuple[datetime, float]]) -> dict[bool, list[float]]:
    """Gennemsnit pr. time på døgnet (weekend?, time) – uden grundforbrugs-trick, til en enkelt måler."""
    acc = {False: [[0.0, 0] for _ in range(24)], True: [[0.0, 0] for _ in range(24)]}
    for t, k in hours:
        if k is None or not 0 <= k <= MAX_HOUR_KWH:
            continue
        a = acc[t.weekday() >= 5][t.hour]
        a[0] += k
        a[1] += 1
    out = {}
    for we in (False, True):
        other = acc[not we]
        out[we] = [a[0] / a[1] if a[1] else (other[h][0] / other[h][1] if other[h][1] else 0.0)
                   for h, a in enumerate(acc[we])]
    return out


@dataclass
class HeatPumpModel:
    """Varmepumpens døgnforbrug: c0 + k × (døgnets gennemsnitlige graddage). Fordeles efter timeprofilen."""
    c0: float = 0.0
    k: float = 0.0
    days: int = 0
    active: bool = False
    avg: dict = field(default_factory=dict)      # weekend? -> 24 timers gennemsnit (seneste 14 dage)
    share: dict = field(default_factory=dict)    # weekend? -> andel af døgnet pr. time
    yesterday: dict = field(default_factory=dict)

    def day_kwh(self, mean_hdd: float) -> float:
        return max(self.c0 + self.k * mean_hdd, 0.0)

    def as_dict(self) -> dict:
        return {"active": self.active, "days": self.days, "base_kwh_day": round(self.c0, 2),
                "kwh_per_degree_day": round(self.k, 2), "yesterday": self.yesterday}


def fit_heatpump(hp_hours: list[tuple[datetime, float]], temps: dict[datetime, float], today,
                 profile_from: datetime | None = None) -> HeatPumpModel:
    """Lær varmepumpens døgnforbrug mod udetemperaturen (vægtet, så de nyeste døgn tæller mest)."""
    m = HeatPumpModel()
    recent = [(t, k) for t, k in hp_hours if profile_from is None or t >= profile_from]
    m.avg = hour_averages(recent)
    for we in (False, True):
        tot = sum(m.avg[we])
        m.share[we] = [v / tot for v in m.avg[we]] if tot > 0 else [1 / 24] * 24
    per_day: dict = {}
    for t, k in hp_hours:
        if k is None or not 0 <= k <= MAX_HOUR_KWH or t not in temps or t.date() >= today:
            continue
        d = per_day.setdefault(t.date(), [0.0, 0.0, 0])
        d[0] += k
        d[1] += hdd(temps[t])
        d[2] += 1
    days = [(d, v[0], v[1] / v[2]) for d, v in per_day.items() if v[2] >= 22]   # (næsten) hele døgn
    m.days = len(days)
    if m.days < HP_MIN_DAYS:
        return m
    w = [0.5 ** ((today - d).days / HP_HALF_LIFE_DAYS) for d, _, _ in days]
    sw = sum(w)
    mx = sum(wi * x for wi, (_, _, x) in zip(w, days)) / sw
    my = sum(wi * y for wi, (_, y, _) in zip(w, days)) / sw
    sxx = sum(wi * (x - mx) ** 2 for wi, (_, _, x) in zip(w, days))
    sxy = sum(wi * (x - mx) * (y - my) for wi, (_, y, x) in zip(w, days))
    m.k = max(sxy / sxx, 0.0) if sxx > 0.5 else 0.0      # for lidt temperaturudsving: kun gennemsnittet
    m.c0 = my - m.k * mx
    m.active = True
    last = max(days)
    pred = m.day_kwh(last[2])
    m.yesterday = {"date": last[0].isoformat(), "expected_kwh": round(pred, 1), "actual_kwh": round(last[1], 1),
                   "error_pct": round((last[1] - pred) / pred * 100) if pred > 0.5 else None}
    return m


def heatpump_hours(m: HeatPumpModel, day: datetime, mean_hdd: float,
                   reduce: set[int] | None = None, boost: set[int] | None = None) -> list[float]:
    """Varmepumpens forventede forbrug time for time en given dag – med SmartHus' sparetimer og ekstra-timer."""
    total = m.day_kwh(mean_hdd)
    out = [total * f for f in m.share[day.weekday() >= 5]]
    reduce, boost = reduce or set(), boost or set()
    moved = 0.0
    for h in sorted(reduce):
        cut = out[h] * (1 - HP_REDUCE_KEEP)
        out[h] -= cut
        moved += cut
    if moved > 0:
        # Den sparede varme hentes ind i ekstra-timerne – ellers i timerne lige efter sparetimerne
        targets = sorted(boost - reduce) or sorted({(max(reduce) + i) % 24 for i in (1, 2, 3)} - reduce)
        for h in targets:
            out[h] += moved / len(targets)
    return out


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


HOLD_MIN_GAIN = 0.10   # kr/kWh – gem kun batteriet, når strømmen er mindst så meget billigere end senere
HOLD_MIN_SLOTS = 2     # gem i mindst 30 min ad gangen (ingen enkelte kvarterer)
HOLD_STICKY_KWH = 0.3  # en igangværende gem-periode fortsætter, til batteriet har mindst så meget for meget


def _solar_in(solar: dict | None, a: datetime, b: datetime) -> float:
    """Forventet solproduktion (kWh) mellem to lokale tidspunkter ud fra en prognose pr. time."""
    if not solar or b <= a:
        return 0.0
    total, t = 0.0, a
    while t < b:
        h = t.replace(minute=0, second=0, microsecond=0)
        nxt = min(h + timedelta(hours=1), b)
        total += solar.get(h, 0.0) * (nxt - t).total_seconds() / 3600
        t = nxt
    return total


SOLAR_CHARGE = 0.95    # tab, når solens overskud lades i batteriet


def plan_hold(slots: list, profile: Profile, now: datetime, until: datetime, usable_kwh: float,
              min_gain: float = HOLD_MIN_GAIN, previous: set | None = None, solar: dict | None = None,
              room_kwh: float | None = None) -> tuple[list, float | None]:
    """Hvornår skal batteriet gemmes (stå stille), så strømmen rækker til de dyreste timer?

    Batteriets brugbare energi – og solens forventede overskud, når det er kommet – fordeles på husets
    forventede forbrug (efter sol) i de dyreste tidsrum frem til `until` (næste opladning). Solens overskud
    kan kun bruges efter, det er lavet, og højst `room_kwh` kan gemmes. Er der ikke nok til alle, gemmes
    batteriet i de tidsrum, det ikke skal dække, når et senere tidsrum, det skal dække, er mindst
    `min_gain` dyrere. Huset kører så på nettet, mens solen stadig må lade batteriet.

    `slots` har tidspunkter med tidszone; `now`/`until` er lokale tidspunkter. `solar` er kWh pr. lokal time.
    `previous` er starttiderne fra sidste beregning. De holdes ved (hysterese), så planen ikke skifter
    frem og tilbage, fordi den regnes om hvert minut.
    Returnerer (tidsrum, hvor batteriet gemmes, prisgrænsen for de tidsrum batteriet dækker).
    """
    previous = previous or set()
    horizon = sorted((s for s in slots if s.end > now and s.start < until), key=lambda s: s.start)
    if not horizon:
        return [], None
    tz = now.tzinfo
    need, pool = [], []
    cap = float("inf") if room_kwh is None else room_kwh
    for s in horizon:
        a, b = max(s.start.astimezone(tz), now), min(s.end.astimezone(tz), until)
        load, sun = expected_load(profile, a, b), _solar_in(solar, a, b)
        need.append(max(load - sun, 0.0) / EFFICIENCY)
        add = min(max(sun - load, 0.0) * SOLAR_CHARGE, cap)
        cap -= add
        pool.append(add)
    if usable_kwh <= 0.05 and not any(pool):
        return [], None
    e0 = usable_kwh
    covered, short = set(), 0.0
    for k in sorted(range(len(horizon)), key=lambda k: (-horizon[k].price, horizon[k].start)):
        n = need[k]
        if n <= 0:
            continue
        took = 0.0
        for j in range(k - 1, -1, -1):          # solens overskud fra før dette tidsrum – det seneste først
            if n <= 0:
                break
            d = min(pool[j], n)
            pool[j] -= d
            n -= d
            took += d
        d = min(e0, n)
        e0 -= d
        n -= d
        took += d
        if took > 0:
            covered.add(horizon[k].start)
        short += n
    spare = e0 + sum(pool)
    if short <= 0 and spare > (HOLD_STICKY_KWH if previous else 0):
        return [], None                       # nok til det hele – intet at gemme
    if not covered:
        return [], None
    limit = min(s.price for s in horizon if s.start in covered)
    # Gem i et tidsrum, batteriet ikke skal dække, hvis det senere skal dække et tidsrum, der er mindst
    # `min_gain` dyrere – ellers ville strømmen blive brugt nu og mangle der.
    later = [0.0] * len(horizon)
    best = float("-inf")
    for k in range(len(horizon) - 1, -1, -1):
        later[k] = best
        if horizon[k].start in covered:
            best = max(best, horizon[k].price)
    flag = [s.start not in covered and need[k] > 0
            and (later[k] - s.price >= min_gain or (s.start in previous and later[k] > s.price))
            for k, s in enumerate(horizon)]
    # Glat ud: korte huller (højst 30 min) mellem to perioder lukkes, hvis batteriet alligevel ikke dækker dem …
    i = 0
    while i < len(horizon):
        if not flag[i]:
            j = i
            while j < len(horizon) and not flag[j]:
                j += 1
            sun_only = all(need[k] <= 0 for k in range(i, j))        # solen dækker huset alene: gem koster intet
            if 0 < i and j < len(horizon) and (sun_only or (j - i <= HOLD_MIN_SLOTS
                                                            and all(horizon[k].start not in covered for k in range(i, j)))):
                for k in range(i, j):
                    flag[k] = True
            i = j
        else:
            i += 1
    # … og korte perioder (under 30 min) droppes, så batteriet ikke skifter for hvert kvarter
    out, run = [], []
    for s, f in zip(horizon, flag):
        if f and (not run or run[-1].end == s.start):
            run.append(s)
            continue
        if len(run) >= HOLD_MIN_SLOTS:
            out += run
        run = [s] if f else []
    if len(run) >= HOLD_MIN_SLOTS:
        out += run
    return out, limit


ROUND_TRIP = 0.90      # ca. 10 % tab fra opladning til afladning


def _blank_day() -> dict:
    return {"house_kwh": 0.0, "house_value": 0.0, "house_cost": 0.0, "sell_kwh": 0.0, "sell_income": 0.0,
            "sell_cost": 0.0, "charge_kwh": 0.0, "charge_cost": 0.0}


def forecast_battery(slots: list, profile: Profile, now: datetime, *, battery_kwh: float, floor_kwh: float,
                     full_kwh: float, charge: list, hold: list, sell: list, stock_price: float,
                     can_hold: bool, solar: dict | None = None, charge_target=None) -> dict:
    """Skøn over batteriet time for time: hvornår det lades, gemmes, dækker huset og sælger – og hvad det sparer.

    * Ladetidsrum fylder batteriet op til `full_kwh` eller det mål, `charge_target(ladeblokkens start)` giver
      (kWh, samme mål som styringen bruger). Ladepris = købsprisen.
    * Gem-tidsrum: batteriet står stille. Efter hver opladning planlægges nye gem-tidsrum (hvis anlægget kan).
    * Ellers dækker batteriet husets forventede forbrug, til det når laveste niveau.
    * I salgstidsrum sælges det, der er ud over husets behov frem til næste opladning.
    Strømmen i batteriet koster gennemsnittet af det, den er købt for, plus ca. 10 % tab.
    Returnerer {"days": {dato: {...}}, "use": [tidsrum hvor batteriet dækker huset], "hold": [gem-tidsrum]}.
    """
    tz = now.tzinfo
    fut = sorted((s for s in slots if s.end > now), key=lambda s: s.start)
    ch = {s.start for s in charge}
    sl = {s.start for s in sell}
    hd = {s.start for s in hold}
    bat, avg = battery_kwh, stock_price
    days: dict[str, dict] = {}
    use: list = []
    sunny: list = []
    sun_cover: list = []
    goals: dict[int, float] = {}

    def next_charge(t: datetime) -> datetime:
        later = [s.start for s in fut if s.start in ch and s.start >= t]
        return (min(later) if later else (fut[-1].end if fut else t)).astimezone(tz)

    def block_end(i: int, keys: set) -> int:
        j = i
        while j + 1 < len(fut) and fut[j + 1].start in keys and fut[j + 1].start == fut[j].end:
            j += 1
        return j

    selling: list = []                               # salgstimer, hvor der forventes noget over reserven
    for i, s in enumerate(fut):
        d = days.setdefault(s.start.astimezone(tz).date().isoformat(), _blank_day())
        a, b = max(s.start.astimezone(tz), now), s.end.astimezone(tz)
        sun = _solar_in(solar, a, b)
        load_all = expected_load(profile, a, b)
        load = max(load_all - sun, 0.0)              # solen dækker huset først
        extra = min(max(sun - load_all, 0.0) * SOLAR_CHARGE, max(full_kwh - bat, 0.0))
        if extra > 0:                                # … og overskuddet lader batteriet
            avg = (avg * max(bat, 0.0) + extra * max(s.sale, 0.0)) / max(bat + extra, 0.01)   # solstrøm koster salgsprisen
            bat += extra
            d["solar_kwh"] = d.get("solar_kwh", 0.0) + extra
            if extra >= 0.1 * (b - a).total_seconds() / 3600:   # mindst ca. 0,1 kW – ellers er det ikke værd at vise
                sunny.append(s)
        if sun > 0.005 and load <= 0.0005 and s not in sunny:
            sun_cover.append(s)                      # solen dækker huset alene – batteriet er fuldt eller står stille
        if s.start in ch:
            j = block_end(i, ch)
            if j not in goals:
                goals[j] = min(charge_target(s.start), full_kwh) if charge_target else full_kwh
            add = max(goals[j] - bat, 0.0) / (j - i + 1)
            if add > 0:
                avg = (avg * max(bat, 0.0) + add * s.price) / max(bat + add, 0.01)
                bat += add
                d["charge_kwh"] += add
                d["charge_cost"] += add * s.price
            if i == j and can_hold:      # opladningen er færdig: hvornår skal batteriet gemmes til næste gang?
                rest = [x for x in fut if x.start not in ch]
                hd |= {x.start for x in plan_hold(rest, profile, b, next_charge(s.end), max(bat - floor_kwh, 0.0),
                                                  solar=solar, room_kwh=max(full_kwh - bat, 0.0))[0]}
            continue
        if s.start in hd:
            continue
        avail = max(bat - floor_kwh, 0.0)
        cost = avg / ROUND_TRIP
        used = min(load, avail)
        sold = 0.0
        if s.start in sl:
            need = expected_load(profile, b, next_charge(s.end))
            left = block_end(i, sl) - i + 1
            sold = max(min((avail - used - need) / left, avail - used), 0.0)
        bat -= used + sold
        if avail > 0.005 and load > 0.0005:  # batteriet dækker huset (også når det bruger meget lidt)
            use.append(s)
            d["house_kwh"] += used
            d["house_value"] += used * s.price
            d["house_cost"] += used * cost
        if sold > 0.005:
            selling.append(s)
            d["sell_kwh"] += sold
            d["sell_income"] += sold * s.sale
            d["sell_cost"] += sold * cost
    for d in days.values():
        d["house_saving"] = d["house_value"] - d["house_cost"]
        d["sell_gain"] = d["sell_income"] - d["sell_cost"]
        for k in list(d):
            d[k] = round(d[k], 2)
    held = [s for s in fut if s.start in hd]
    return {"days": days, "use": use, "hold": held, "solar": sunny, "sun_cover": sun_cover, "sell": selling}


def _hour_steps(start: datetime, end: datetime):
    """(timens start, andel af timen) fra start til end."""
    t = start
    while t < end:
        h = t.replace(minute=0, second=0, microsecond=0)
        nxt = min(h + timedelta(hours=1), end)
        yield h, (nxt - t).total_seconds() / 3600
        t = nxt


def simulate_battery(e0: float, profile: Profile, solar: dict, start: datetime, end: datetime, *,
                     floor: float, full: float) -> tuple[float, float]:
    """Batteriet time for time: huset trækker sit forbrug, solen lægger til (højst op til fuldt).
    Returnerer (laveste niveau undervejs, niveau ved end)."""
    e, low = e0, e0
    for h, frac in _hour_steps(start, end):
        load = profile.hours_for(h)[h.hour] * frac
        sun = (solar.get(h) or 0.0) * frac
        e = min(full, e + sun - load)
        low = min(low, e)
    return low, e


def battery_need_at(profile: Profile, solar: dict, start: datetime, end: datetime, *,
                    floor: float, full: float) -> float:
    """Mindste energi (kWh) i batteriet ved `start`, så det med solens hjælp dækker huset frem til `end`
    uden at komme under `floor`. Højst `full` – kan selv et fuldt batteri ikke klare det, er svaret `full`."""
    def ok(e0: float) -> bool:
        low, _ = simulate_battery(e0, profile, solar, start, end, floor=floor, full=full)
        return low >= floor - 1e-6
    if ok(floor):
        return floor
    if not ok(full):
        return full
    lo, hi = floor, full
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if ok(mid) else (mid, hi)
    return hi

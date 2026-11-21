"""Lær forskellen mellem solprognosen og anlæggets faktiske produktion, time for time.

Prognoser kender sjældent anlægget: retning, skygge, tab og vekselretterens grænse. Derfor gemmes
prognosen for hver time (som den så ud ved midnat), og hver nat sammenlignes den med den målte produktion.
Det giver en faktor for hver time på døgnet, som prognosen ganges med. Faktoren består af to dele:
dagens forløb, som læres hurtigt, og dagens samlede niveau, som læres langsomt, fordi vejret fylder mest dér.

De seneste dage vejer mest (halveringstid HALF_LIFE dage), og klare timer mere end overskyede: skygge
(træer, når solen står lavt om vinteren) tager kun det direkte sollys og viser sig derfor på klare dage. Så slår
en ændring – skyggen om efteråret, og at den forsvinder igen om foråret – igennem på få klare dage.
Hvor klar en time var, kommer fra vejrdata (andelen af direkte stråling, Open-Meteo). Uden vejrdata vejer alle
timer ens efter alder.

Timerne er i UTC, så solens gang ikke flytter sig, når Danmark skifter mellem sommer- og vintertid.
Ingen afhængighed af Home Assistant, så logikken kan testes for sig.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

WINDOW_DAYS = 21       # kun de seneste dage tæller, så faktorerne følger årstiden
KEEP_DAYS = 30         # så længe gemmes prognosen
MIN_DAYS = 3           # færre brugbare dage end dette: prognosen bruges uændret
SHAPE_TRUST_DAYS = 1.0 # forløbet over dagen: vægt = dage / (dage + 1), ca. 90 % efter 10 dage
LEVEL_TRUST_DAYS = 10.0 # dagens niveau: vægt = dage / (dage + 10), 50 % efter 10 dage og ca. 68 % efter 21
HOUR_PRIOR = 0.01      # timer med lille prognose læner sig op ad dagens samlede forhold
MAX_FACTOR = 3.0       # værn mod meget små tal i prognosen
MAX_HOUR_KWH = 100.0   # urealistisk time (fx nulstillet måler) springes over
MIN_DAY_FORECAST = 0.2 # kWh – døgn med næsten ingen forventet sol siger ikke noget
MIN_DAY_SHARE = 0.05   # under 5 % af prognosen hele dagen: udfald, sne eller lignende – ikke anlæggets evne
HALF_LIFE = 4.0        # dage – en dag for 4 dage siden vejer det halve af i går
CLEAR_LO, CLEAR_HI = 0.15, 0.55   # andel direkte stråling: under = overskyet, over = klart
CLOUDY_WEIGHT = 0.3    # en helt overskyet time vejer 30 % af en klar
LEVEL_TRUST_WEIGHTED = 3.0  # niveauet med vægtede dage: vægt = dage / (dage + 3)
CLEAR_OBS = 0.55       # andel direkte stråling, før en time regnes som klar (til loftet)
CAP_DAYS = 6           # loft: det mest, anlægget har givet i timen på en klar dag de seneste 6 dage …
CAP_MARGIN = 1.1       # … + 10 % – skygge (træer) slår igennem efter én klar dag og forsvinder lige så hurtigt
OBS_KEEP_DAYS = 400    # klare timer gemmes et år (til senere: skygge efter solens position)


_PER_SOURCE = ("forecast", "curtailed", "factors", "day_factor", "days")


def hour_key(t: datetime) -> str:
    """Timens nøgle i UTC, fx 2026-11-04T08."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _key_time(key: str) -> datetime:
    return datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def clear_sky(t: datetime, lat: float, lon: float) -> float:
    """Klarvejrsstråling (W/m², Haurwitz) i gennemsnit over timen, der begynder `t` – til at sammenligne
    samme time på forskellige dage (solen står lavere hen mod jul og højere mod sommer)."""
    t = t.astimezone(timezone.utc)
    tot = 0.0
    for m in range(5, 60, 10):
        d = t + timedelta(minutes=m)
        decl = math.radians(23.44 * math.sin(math.radians(360 / 365 * (d.timetuple().tm_yday - 81))))
        ha = math.radians(15 * (d.hour + d.minute / 60 + lon / 15 - 12))
        z = (math.sin(math.radians(lat)) * math.sin(decl)
             + math.cos(math.radians(lat)) * math.cos(decl) * math.cos(ha))
        tot += 1098 * z * math.exp(-0.057 / z) if z > 0.01 else 0.0
    return tot / 6


class SolarCalibration:
    """Gemte prognoser og de indlærte faktorer. `data` kan gemmes direkte som JSON."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if isinstance(data, dict) else {}
        for key, empty in (("forecast", {}), ("curtailed", [])):
            self.data.setdefault(key, empty)
        self.location: tuple[float, float] | None = None   # (breddegrad, længdegrad) – til loftet

    def cap(self, t: datetime) -> float | None:
        """Loft for timen (kWh): det mest, anlægget har givet i samme time på en klar dag de seneste
        CAP_DAYS dage, skaleret til solens højde den dag + CAP_MARGIN. None: ingen klar dag at gå ud fra."""
        obs = self.data.get("clear_obs") or {}
        if not obs or self.location is None:
            return None
        lat, lon = self.location
        t = t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        cs_t = clear_sky(t, lat, lon)
        if cs_t <= 0:
            return None
        best = None
        for d in range(1, CAP_DAYS + 1):
            k = hour_key(t - timedelta(days=d))
            o = obs.get(k)
            if not o or o[1] <= 0:
                continue
            v = o[0] * cs_t / o[1]
            best = v if best is None or v > best else best
        return None if best is None else best * CAP_MARGIN

    @property
    def active(self) -> bool:
        return bool(self.data.get("factors"))

    @property
    def days(self) -> int:
        return int(self.data.get("days") or 0)

    def factor(self, t: datetime) -> float:
        """Faktoren for det tidspunkt. 1,0 indtil der er lært nok."""
        factors = self.data.get("factors")
        return factors[t.astimezone(timezone.utc).hour] if factors else 1.0

    def set_source(self, source: str) -> bool:
        """Hver prognose har sin egen læring. Ved skift gemmes den gamle og den nye hentes frem, så et kort
        udfald (fx lige efter genstart, hvor prognosen ikke er klar) ikke sletter det lærte.
        Returnerer True, hvis kilden blev skiftet."""
        old = self.data.get("source")
        self.data["source"] = source
        if old is None or old == source:
            return False
        saved = self.data.setdefault("saved", {})
        saved[old] = {k: self.data.get(k) for k in _PER_SOURCE}
        self.data.update(saved.pop(source, None) or {"forecast": {}, "curtailed": [], "factors": None,
                                                     "day_factor": None, "days": 0})
        return True

    def record(self, hours: dict[datetime, float], now: datetime) -> None:
        """Gem prognosen. En dag fastfryses ved midnat, så det er den prognose, planen faktisk brugte."""
        fc = self.data["forecast"]
        today = now.date()
        for h, kwh in hours.items():
            if h <= now or kwh <= 0:
                continue
            key = hour_key(h)
            if h.astimezone(now.tzinfo).date() > today or key not in fc:
                fc[key] = round(kwh, 3)

    def backfill(self, hours: dict[str, float]) -> None:
        """Fyld huller med tidligere prognose fra historikken. Prognoser, der er gemt undervejs, vinder."""
        fc = self.data["forecast"]
        for key, kwh in hours.items():
            if kwh > 0 and key not in fc:
                fc[key] = round(kwh, 3)

    def uncurtail(self, keys: list[str]) -> None:
        """Timer, der alligevel tæller (fx en "rim"-morgen, der viste sig at være skygge)."""
        if keys:
            drop = set(keys)
            self.data["curtailed"] = [k for k in self.data["curtailed"] if k not in drop]

    def mark_curtailed(self, now: datetime) -> None:
        """Timen tæller ikke: SmartHus har selv skruet ned for solcellerne, så målingen er ikke anlæggets evne."""
        key = hour_key(now)
        if key not in self.data["curtailed"]:
            self.data["curtailed"].append(key)

    def learn(self, actual: dict[str, float], now: datetime, sky: dict[str, float] | None = None) -> None:
        """Beregn faktorerne ud fra `actual` (kWh pr. timenøgle) for hele dage før i dag.
        `sky`: andelen af direkte stråling pr. timenøgle (0–1) – klare timer vejer mest."""
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        oldest, keep = today - timedelta(days=WINDOW_DAYS), today - timedelta(days=KEEP_DAYS)
        fc = self.data["forecast"]
        self.data["forecast"] = fc = {k: v for k, v in fc.items() if _key_time(k) >= keep}
        self.data["curtailed"] = [k for k in self.data["curtailed"] if _key_time(k) >= keep]
        skip = set(self.data["curtailed"])
        by_day: dict[str, list[tuple[int, float, float]]] = {}
        for key, f in fc.items():
            t = _key_time(key)
            a = actual.get(key)
            if not oldest <= t < today or a is None or key in skip or not 0 <= a <= MAX_HOUR_KWH:
                continue
            by_day.setdefault(key[:10], []).append((t.hour, f, a))
        # Klare timer (til loftet og senere til skygge efter solens position)
        if sky and self.location is not None:
            obs = self.data.setdefault("clear_obs", {})
            lat, lon = self.location
            for rows_day, rows in by_day.items():
                for hour, f, a in rows:
                    k = f"{rows_day}T{hour:02d}"
                    if (sky.get(k) or 0.0) >= CLEAR_OBS:
                        cs = clear_sky(_key_time(k), lat, lon)
                        if cs > 20:
                            obs[k] = [round(a, 3), round(cs, 1)]
            limit = today - timedelta(days=OBS_KEEP_DAYS)
            self.data["clear_obs"] = {k: v for k, v in obs.items() if _key_time(k) >= limit}
        f_h, a_h, days, eff = [0.0] * 24, [0.0] * 24, 0, 0.0
        for day, rows in by_day.items():
            f_day, a_day = sum(r[1] for r in rows), sum(r[2] for r in rows)
            if f_day < MIN_DAY_FORECAST or a_day < MIN_DAY_SHARE * f_day:
                continue
            days += 1
            age = max((today - datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days, 0)
            rec = 0.5 ** (age / HALF_LIFE)
            wf = 0.0
            for hour, f, a in rows:
                share = sky.get(f"{day}T{hour:02d}") if sky else None
                if share is None:
                    w_sky = 1.0
                else:
                    clear = min(max((share - CLEAR_LO) / (CLEAR_HI - CLEAR_LO), 0.0), 1.0)
                    w_sky = CLOUDY_WEIGHT + (1.0 - CLOUDY_WEIGHT) * clear
                f_h[hour] += rec * w_sky * f
                a_h[hour] += rec * w_sky * a
                wf += w_sky * f
            eff += rec * wf / f_day
        self.data["days"] = days
        self.data["weighted_days"] = round(eff, 2)
        if days < MIN_DAYS:
            self.data.update(factors=None, day_factor=None)
            return
        f_all, a_all = sum(f_h), sum(a_h)
        ratio = a_all / f_all
        prior = HOUR_PRIOR * f_all / max(eff, 0.1)
        # Dagens forløb: hvordan solen fordeler sig over dagen. Det er anlæggets egenskab og læres hurtigt.
        shape_weight = eff / (eff + SHAPE_TRUST_DAYS)
        shape = [1.0 + shape_weight * (min((a_h[h] + prior * ratio) / (f_h[h] + prior) / ratio if ratio > 0 else 1.0,
                                           MAX_FACTOR) - 1.0) for h in range(24)]
        same_energy = f_all / sum(s * f for s, f in zip(shape, f_h))
        # Dagens samlede niveau: her fylder vejret mest, så det kræver mange flere dage at se anlæggets andel.
        level = 1.0 + eff / (eff + LEVEL_TRUST_WEIGHTED) * (min(ratio, MAX_FACTOR) - 1.0)
        self.data["factors"] = [round(min(s * same_energy * level, MAX_FACTOR), 3) for s in shape]
        self.data["day_factor"] = round(level, 3)

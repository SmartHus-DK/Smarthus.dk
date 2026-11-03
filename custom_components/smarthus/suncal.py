"""Lær forskellen mellem solprognosen og anlæggets faktiske produktion, time for time.

Prognoser kender sjældent anlægget: retning, skygge, tab og vekselretterens grænse. Derfor gemmes
prognosen for hver time (som den så ud ved midnat), og hver nat sammenlignes den med den målte produktion.
Det giver en faktor for hver time på døgnet, som prognosen ganges med. Faktoren består af to dele:
dagens forløb, som læres hurtigt, og dagens samlede niveau, som læres langsomt, fordi vejret fylder mest dér.

Timerne er i UTC, så solens gang ikke flytter sig, når Danmark skifter mellem sommer- og vintertid.
Ingen afhængighed af Home Assistant, så logikken kan testes for sig.
"""

from __future__ import annotations

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


def hour_key(t: datetime) -> str:
    """Timens nøgle i UTC, fx 2026-11-04T08."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _key_time(key: str) -> datetime:
    return datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


class SolarCalibration:
    """Gemte prognoser og de indlærte faktorer. `data` kan gemmes direkte som JSON."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if isinstance(data, dict) else {}
        for key, empty in (("forecast", {}), ("curtailed", [])):
            self.data.setdefault(key, empty)

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
        """Skiftes prognose, gælder det lærte ikke længere. Returnerer True, hvis der blev nulstillet."""
        old = self.data.get("source")
        self.data["source"] = source
        if old is None or old == source:
            return False
        self.data.update(forecast={}, curtailed=[], factors=None, day_factor=None, days=0)
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

    def mark_curtailed(self, now: datetime) -> None:
        """Timen tæller ikke: SmartHus har selv skruet ned for solcellerne, så målingen er ikke anlæggets evne."""
        key = hour_key(now)
        if key not in self.data["curtailed"]:
            self.data["curtailed"].append(key)

    def learn(self, actual: dict[str, float], now: datetime) -> None:
        """Beregn faktorerne ud fra `actual` (kWh pr. timenøgle) for hele dage før i dag."""
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
        f_h, a_h, days = [0.0] * 24, [0.0] * 24, 0
        for rows in by_day.values():
            f_day, a_day = sum(r[1] for r in rows), sum(r[2] for r in rows)
            if f_day < MIN_DAY_FORECAST or a_day < MIN_DAY_SHARE * f_day:
                continue
            days += 1
            for hour, f, a in rows:
                f_h[hour] += f
                a_h[hour] += a
        self.data["days"] = days
        if days < MIN_DAYS:
            self.data.update(factors=None, day_factor=None)
            return
        f_all, a_all = sum(f_h), sum(a_h)
        ratio = a_all / f_all
        prior = HOUR_PRIOR * f_all / days
        # Dagens forløb: hvordan solen fordeler sig over dagen. Det er anlæggets egenskab og læres hurtigt.
        shape_weight = days / (days + SHAPE_TRUST_DAYS)
        shape = [1.0 + shape_weight * (min((a_h[h] + prior * ratio) / (f_h[h] + prior) / ratio if ratio > 0 else 1.0,
                                           MAX_FACTOR) - 1.0) for h in range(24)]
        same_energy = f_all / sum(s * f for s, f in zip(shape, f_h))
        # Dagens samlede niveau: her fylder vejret mest, så det kræver mange flere dage at se anlæggets andel.
        level = 1.0 + days / (days + LEVEL_TRUST_DAYS) * (min(ratio, MAX_FACTOR) - 1.0)
        self.data["factors"] = [round(min(s * same_energy * level, MAX_FACTOR), 3) for s in shape]
        self.data["day_factor"] = round(level, 3)

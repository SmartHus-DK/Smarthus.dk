"""Sne eller rim på solcellerne (ingen Home Assistant-afhængigheder).

Solprognoser regner med, at panelerne er rene. Ligger der sne på dem, giver de næsten intet, til sneen er
gledet af eller smeltet – og efter en frostnat kan rim tage en stor del af formiddagens sol. Uden at vide det
regner planen med sol, der ikke kommer, og springer måske nattens opladning over.

Vejrdata (Open-Meteo) time for time: temperatur, snefald og snedybde.

* Sne: ligger der sne (snedybde ≥ 1 cm), eller er der faldet sne inden for 12 timer, regnes der næsten ikke med
  sol – til panelerne beviseligt producerer igen (mindst 40 % af det forventede i 10 minutter efter sidste snefald).
* Temperaturen: Open-Meteos temperatur gælder et større område og kan ramme flere grader ved siden af en kold,
  klar morgen. Husets egen udeføler (ellers vejr-entiteten i Home Assistant) retter den: forskellen nu lægges på
  de nærmeste timer og aftager over 6 timer.
* Rim: en time med frost (højst 0,5 °C) efter en nat med frost regnes med den andel af solen, anlægget plejer at
  give i sådanne timer. Den andel læres: hver frostmorgen sammenlignes målingen med prognosen (start 60 %).
  Producerer panelerne fuldt, mens det er frost, er de rene resten af dagen.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SNOW_DEPTH_M = 0.01        # 1 cm sne på jorden: regn med sne på panelerne
SNOWFALL_CM = 0.5          # så meget nyt sne inden for SNOW_WINDOW_H dækker også panelerne
SNOW_WINDOW_H = 12
SNOW_FACTOR = 0.05         # næsten ingen sol, mens panelerne er dækket
FROST_T = 0.5              # °C – rim kan sidde på panelerne
NIGHT_FROST_T = 0.0        # og natten (de foregående 12 timer) skal have haft frost
FROST_START = 0.6          # andel af solen i frosttimer, før anlægget er lært
FROST_ALPHA = 0.3          # hvor hurtigt den andel følger nye frostmorgener
FROST_MIN_KWH = 0.5        # så meget forventet sol i frosttimer, før en morgen tæller
PROOF_SHARE = 0.4          # panelerne producerer: mindst 40 % af det forventede …
PROOF_MIN_KW = 0.3         # … når der forventes mindst 0,3 kW …
PROOF_SECONDS = 600        # … i 10 minutter
OFFSET_HOURS = 6.0         # målt udetemperatur retter Open-Meteo – fuldt nu, aftagende til 0 efter 6 timer
OFFSET_MAX_AGE = 3600      # en måling ældre end en time bruges ikke


class PanelCover:
    """Holder øje med sne og rim. `data` kan gemmes direkte som JSON."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if isinstance(data, dict) else {}
        self.data.setdefault("frost_ratio", FROST_START)
        self.wx: dict[datetime, tuple[float | None, float, float]] = {}   # time → (temp, snefald cm, snedybde m)
        self._ok_since: datetime | None = None
        self._measured: tuple[datetime, float] | None = None

    def set_measured(self, now: datetime, temp: float | None) -> None:
        """Udetemperaturen målt nu (egen føler eller vejr-entitet) – retter Open-Meteos temperatur."""
        if temp is not None:
            self._measured = (now, float(temp))

    def _offset(self, h: datetime) -> float:
        m = self._measured
        if m is None:
            return 0.0
        t0, temp = m
        base = self.wx.get(t0.replace(minute=0, second=0, microsecond=0))
        if base is None or base[0] is None or abs((h - t0).total_seconds()) > OFFSET_HOURS * 3600:
            return 0.0
        hours = abs((h.replace(minute=0, second=0, microsecond=0)
                     - t0.replace(minute=0, second=0, microsecond=0)).total_seconds()) / 3600
        return (temp - base[0]) * max(1.0 - hours / OFFSET_HOURS, 0.0)

    def temp(self, h: datetime) -> float | None:
        """Temperaturen for timen: Open-Meteo, rettet med den målte temperatur."""
        cur = self._at(h)
        if cur is None or cur[0] is None:
            return None
        return cur[0] + self._offset(h)

    def offset_now(self, now: datetime) -> float | None:
        m = self._measured
        if m is None or (now - m[0]).total_seconds() > OFFSET_MAX_AGE:
            return None
        return round(self._offset(m[0]), 1)

    # --- vejr -------------------------------------------------------------------------------------------
    def set_weather(self, wx: dict[datetime, tuple[float | None, float, float]]) -> None:
        if wx:
            self.wx = wx

    def _at(self, h: datetime) -> tuple[float | None, float, float] | None:
        return self.wx.get(h.replace(minute=0, second=0, microsecond=0))

    def _proof(self) -> datetime | None:
        p = self.data.get("proof")
        try:
            return datetime.fromisoformat(p) if p else None
        except ValueError:
            return None

    def _last_snowfall(self, h: datetime) -> datetime | None:
        last = None
        for t, (_, fall, _) in self.wx.items():
            if t <= h and (fall or 0) >= 0.1 and (last is None or t > last):
                last = t
        return last

    def state(self, h: datetime) -> str | None:
        """"snow", "frost" eller None for timen, der begynder `h`."""
        cur = self._at(h)
        if cur is None:
            return None
        _, _, depth = cur
        temp = self.temp(h)
        proof = self._proof()
        hh = h.replace(minute=0, second=0, microsecond=0)
        window = [self.wx.get(hh - timedelta(hours=i)) for i in range(SNOW_WINDOW_H + 1)]
        fall = sum((w[1] or 0) for w in window if w)
        if (depth or 0) >= SNOW_DEPTH_M or fall >= SNOWFALL_CM:
            last = self._last_snowfall(h)
            if proof is None or (last is not None and proof < last + timedelta(hours=1)):
                return "snow"
        if temp is not None and temp <= FROST_T:
            night = [t for t in (self.temp(hh - timedelta(hours=i)) for i in range(SNOW_WINDOW_H + 1)) if t is not None]
            if night and min(night) <= NIGHT_FROST_T:
                if proof is not None and proof.date() == h.date() and proof <= h:
                    return None                       # rimen er væk i dag
                return "frost"
        return None

    def factor(self, h: datetime) -> float:
        st = self.state(h)
        if st == "snow":
            return SNOW_FACTOR
        if st == "frost":
            return min(max(float(self.data.get("frost_ratio") or FROST_START), 0.05), 1.0)
        return 1.0

    # --- målinger -----------------------------------------------------------------------------------------
    def observe(self, now: datetime, measured_kw: float | None, expected_kw: float | None, seconds: float) -> None:
        """Hvert halve minut: produktionen nu og prognosen (uden sne/rim) for samme tidspunkt.
        Producerer panelerne, er de rene. Frosttimer lægges sammen til læringen af rim."""
        if measured_kw is None or expected_kw is None:
            self._ok_since = None
            return
        st = self.state(now)
        if st == "frost" and expected_kw > 0:
            day = now.date().isoformat()
            acc = self.data.get("frost_day")
            if not acc or acc.get("day") != day:
                acc = self.data["frost_day"] = {"day": day, "measured": 0.0, "expected": 0.0}
            hours = min(max(seconds, 0.0), 180.0) / 3600
            acc["measured"] += max(measured_kw, 0.0) * hours
            acc["expected"] += expected_kw * hours
        if expected_kw >= PROOF_MIN_KW and measured_kw >= PROOF_SHARE * expected_kw:
            if self._ok_since is None:
                self._ok_since = now
            elif (now - self._ok_since).total_seconds() >= PROOF_SECONDS and st is not None:
                self.data["proof"] = now.isoformat()
        else:
            self._ok_since = None

    def learn(self, now: datetime) -> None:
        """Om natten: læg gårsdagens frosttimer ind i andelen af sol, anlægget giver med rim."""
        acc = self.data.get("frost_day")
        if not acc or acc.get("day") == now.date().isoformat():
            return
        self.data.pop("frost_day", None)
        if acc.get("expected", 0.0) < FROST_MIN_KWH:
            return
        ratio = min(max(acc["measured"] / acc["expected"], 0.05), 1.0)
        old = float(self.data.get("frost_ratio") or FROST_START)
        self.data["frost_ratio"] = round(old + FROST_ALPHA * (ratio - old), 3)
        self.data["frost_days"] = int(self.data.get("frost_days") or 0) + 1

    def summary(self, hours: list[datetime], now: datetime) -> dict | None:
        """Til forklaringen: hvad der gælder nu og de kommende timer med sol."""
        states = [(h, self.state(h)) for h in hours if h + timedelta(hours=1) > now]
        hit = [(h, s) for h, s in states if s]
        if not hit:
            return None
        kind = "snow" if any(s == "snow" for _, s in hit) else "frost"
        rel = [h for h, s in hit if s == kind]
        return {"state": kind, "from": min(rel).isoformat(), "until": (max(rel) + timedelta(hours=1)).isoformat(),
                "now": self.state(now) == kind, "frost_ratio": self.data.get("frost_ratio"),
                "temp_offset": self.offset_now(now),
                "frost_days": int(self.data.get("frost_days") or 0)}

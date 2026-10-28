"""Beregning af samlet elpris ud fra spotpris, tariffer og afgifter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

from .const import (
    CONF_ELAFGIFT,
    CONF_ENERGINET,
    CONF_MARKUP,
    CONF_SALE_FEE,
    CONF_T_SUMMER_HIGH,
    CONF_T_SUMMER_LOW,
    CONF_T_SUMMER_PEAK,
    CONF_T_WINTER_HIGH,
    CONF_T_WINTER_LOW,
    CONF_T_WINTER_PEAK,
    CONF_VAT,
    DEFAULT_PRICES,
)
from .planner import Slot
from .tariffs import Tariffs


def parse_price_list(raw: list | None) -> list[tuple[datetime, float]]:
    """Læs en prisliste fra fx Strømligning: [{"start": "...", "price": 2.15}, ...]."""
    out: list[tuple[datetime, float]] = []
    for item in raw or []:
        try:
            start = item.get("start") if isinstance(item, dict) else None
            price = item.get("price") if isinstance(item, dict) else None
            if start is None or price is None:
                continue
            t = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            out.append((t.astimezone(timezone.utc), float(price)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    # Nogle kilder angiver øre/kWh – omregn til kr/kWh
    if out:
        vals = sorted(p for _, p in out)
        if vals[len(vals) // 2] > 20:
            out = [(t, p / 100) for t, p in out]
    return out


TOMORROW_FROM_HOUR = 12.5   # priserne for i morgen kommer normalt ca. kl. 13


def missing_tomorrow(slots: list[Slot], now_local: datetime) -> bool:
    """Er det efter ca. kl. 12.30, og mangler vi stadig priser for i morgen?"""
    if now_local.hour + now_local.minute / 60 < TOMORROW_FROM_HOUR:
        return False
    tomorrow = (now_local + timedelta(days=1)).date()
    return not any(s.start.astimezone(now_local.tzinfo).date() == tomorrow for s in slots)


def apply_external_prices(slots: list[Slot], prices: list[tuple[datetime, float]]) -> tuple[list[Slot], int]:
    """Erstat købsprisen i hvert tidsrum med den eksterne pris, der gælder på tidspunktet.

    Returnerer de nye tidsrum og hvor mange der fik en ekstern pris. Tidsrum uden ekstern
    pris (fx efter sidste kendte pris) beholder den beregnede pris.
    """
    if not prices:
        return slots, 0
    starts = [t for t, _ in prices]
    # Varighed pr. ekstern pris = afstand til næste (sidste bruger samme varighed som den før)
    durs = [b - a for a, b in zip(starts, starts[1:])] or [timedelta(hours=1)]
    durs.append(durs[-1])
    out, hits, j = [], 0, 0
    for s in slots:
        while j + 1 < len(prices) and prices[j + 1][0] <= s.start:
            j += 1
        t, p = prices[j]
        if t <= s.start < t + durs[j]:
            out.append(Slot(s.start, s.end, s.spot, round(p, 4), s.sale))
            hits += 1
        else:
            out.append(s)
    return out, hits


def apply_external_spot(slots: list[Slot], prices: list[tuple[datetime, float]],
                        vat_included: bool, sale_fee: float) -> tuple[list[Slot], int]:
    """Brug ekstern spotpris (fx Strømligning) til spot- og salgspris. Salg er altid uden moms."""
    if not prices:
        return slots, 0
    starts = [t for t, _ in prices]
    durs = [b - a for a, b in zip(starts, starts[1:])] or [timedelta(hours=1)]
    durs.append(durs[-1])
    out, hits, j = [], 0, 0
    for s in slots:
        while j + 1 < len(prices) and prices[j + 1][0] <= s.start:
            j += 1
        t, p = prices[j]
        if t <= s.start < t + durs[j]:
            spot = p / 1.25 if vat_included else p
            out.append(Slot(s.start, s.end, round(spot, 4), s.price, round(spot - sale_fee, 4)))
            hits += 1
        else:
            out.append(s)
    return out, hits


# Elafgift (kr/kWh uden moms) for almindeligt forbrug – bruges når gammel historik prisfastsættes.
# Kilde: Skatteforvaltningens satser. (fra-dato, sats); den seneste dato før tidspunktet gælder.
ELAFGIFT_HISTORY: tuple[tuple[str, float], ...] = (
    ("2022-01-01", 0.903),
    ("2022-07-01", 0.763),
    ("2022-10-01", 0.723),
    ("2023-01-01", 0.008),
    ("2023-07-01", 0.697),
    ("2024-01-01", 0.761),
    ("2025-01-01", 0.720),
    ("2026-01-01", 0.008),
)


def elafgift_at(when: datetime, tz: tzinfo | None = None) -> float | None:
    """Elafgiften på et tidspunkt ud fra tabellen (None efter tabellens sidste kendte år)."""
    d = (when.astimezone(tz) if tz else when).date().isoformat()
    if d >= "2028-01-01":
        return None  # ukendt – brug den indstillede sats
    rate = ELAFGIFT_HISTORY[0][1]
    for start, r in ELAFGIFT_HISTORY:
        if d >= start:
            rate = r
    return rate


class PriceModel:
    """Samlet pris = (spot + leverandørtillæg + nettarif + Energinet + elafgift) × moms."""

    def __init__(self, config: dict, tz: tzinfo) -> None:
        self.c = {**DEFAULT_PRICES, **{k: v for k, v in config.items() if k in DEFAULT_PRICES}}
        self.tz = tz
        self.tariffs: Tariffs | None = None      # hentet fra Energi Data Service (valgt netselskab)

    def net_tariff(self, when: datetime) -> float:
        """Netselskabets tarif: hentet, eller Tarifmodel 3.0 med de indtastede satser."""
        if self.tariffs and (v := self.tariffs.net(when)) is not None:
            return v
        local = when.astimezone(self.tz)
        summer = 4 <= local.month <= 9
        h = local.hour
        if h < 6:
            key = CONF_T_SUMMER_LOW if summer else CONF_T_WINTER_LOW
        elif 17 <= h < 21:
            key = CONF_T_SUMMER_PEAK if summer else CONF_T_WINTER_PEAK
        else:
            key = CONF_T_SUMMER_HIGH if summer else CONF_T_WINTER_HIGH
        return float(self.c[key])

    def buy_price(self, spot: float, when: datetime, historic: bool = False) -> float:
        """Købspris. `historic`: brug den elafgift, der gjaldt på tidspunktet (til import af historik)."""
        afgift = float(self.c[CONF_ELAFGIFT])
        energinet = float(self.c[CONF_ENERGINET])
        if self.tariffs and (v := self.tariffs.elafgift(when)) is not None:
            afgift = v
        elif historic and (old := elafgift_at(when, self.tz)) is not None:
            afgift = old
        if self.tariffs and (v := self.tariffs.transmission(when)) is not None:
            energinet = v
        total = (
            spot
            + float(self.c[CONF_MARKUP])
            + self.net_tariff(when)
            + energinet
            + afgift
        )
        if self.c[CONF_VAT]:
            total *= 1.25
        return round(total, 4)

    def sale_price(self, spot: float) -> float:
        return round(spot - float(self.c[CONF_SALE_FEE]), 4)

    def build_slots(self, records: list[dict]) -> list[Slot]:
        """Lav tidsrum ud fra Energi Data Service-poster (TimeUTC, DayAheadPriceDKK)."""
        points: list[tuple[datetime, float]] = []
        for r in records:
            raw = r.get("DayAheadPriceDKK")
            ts = r.get("TimeUTC")
            if raw is None or not ts:
                continue
            start = datetime.fromisoformat(ts)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            points.append((start.astimezone(timezone.utc), float(raw) / 1000))
        points.sort(key=lambda p: p[0])
        if not points:
            return []
        diffs = [b[0] - a[0] for a, b in zip(points, points[1:]) if b[0] > a[0]]
        res = min(diffs) if diffs else timedelta(minutes=15)
        slots: list[Slot] = []
        seen = set()
        for start, spot in points:
            if start in seen:
                continue
            seen.add(start)
            slots.append(
                Slot(
                    start=start,
                    end=start + res,
                    spot=round(spot, 4),
                    price=self.buy_price(spot, start),
                    sale=self.sale_price(spot),
                )
            )
        return slots


def has_vat(setting: str | None, entity_id: str | None) -> bool:
    """Er sensorens priser med moms? Valget i opsætningen gælder. Ældre opsætninger uden valg
    falder tilbage på navnet (Strømligning bruger "_vat" for priser med moms)."""
    if setting in ("incl", "excl"):
        return setting == "incl"
    return (entity_id or "").endswith("_vat")


def vat_factor(source_has_vat: bool, want_vat: bool) -> float:
    """Faktor der omregner en ekstern pris til SmartHus' prisgrundlag (med eller uden moms)."""
    if want_vat and not source_has_vat:
        return 1.25
    if source_has_vat and not want_vat:
        return 1 / 1.25
    return 1.0

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


class PriceModel:
    """Samlet pris = (spot + leverandørtillæg + nettarif + Energinet + elafgift) × moms."""

    def __init__(self, config: dict, tz: tzinfo) -> None:
        self.c = {**DEFAULT_PRICES, **{k: v for k, v in config.items() if k in DEFAULT_PRICES}}
        self.tz = tz

    def net_tariff(self, when: datetime) -> float:
        """Tarifmodel 3.0: lav 00–06, høj 06–17, spids 17–21, høj 21–24."""
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

    def buy_price(self, spot: float, when: datetime) -> float:
        total = (
            spot
            + float(self.c[CONF_MARKUP])
            + self.net_tariff(when)
            + float(self.c[CONF_ENERGINET])
            + float(self.c[CONF_ELAFGIFT])
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

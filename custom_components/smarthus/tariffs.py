"""Tariffer og elafgift fra Energi Data Service (DatahubPricelist) – ingen Home Assistant-afhængigheder.

Netselskabets nettarif (C-kunder, timedifferentieret), Energinets transmissions- og systemtarif
og elafgiften hentes med gyldighedsperioder, så både dagens og ældre priser bliver rigtige
(bruges også, når energioverblikket regnes bagud).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, tzinfo

ENERGINET_GLN = "5790000432752"
ENERGINET_CODES = ("40000", "41000")      # transmissions nettarif + systemtarif
ELAFGIFT_CODE = "EA-001"
DATASET_URL = "https://api.energidataservice.dk/dataset/DatahubPricelist"

# Netselskaber: nøgle → (navn, GLN-nummer, tarifkode for C-kunder). Kilde: DatahubPricelist, oktober 2026.
GRID_COMPANIES: dict[str, tuple[str, str, str]] = {
    "cerius": ("Cerius", "5790000705184", "30TR_C_ET"),
    "dinel": ("Dinel", "5790000610099", "TCL<100_02"),
    "elektrus": ("Elektrus", "5790000836239", "6000091"),
    "elinord": ("Elinord", "5790001095277", "43300"),
    "elnet_midt": ("Elnet Midt", "5790001100520", "T3001"),
    "flow": ("FLOW Elnet", "5790000392551", "FE1 NT-01"),
    "forsyning_elnet": ("Forsyning Elnet", "5790001088309", "STR-NT-03"),
    "grindsted": ("Grindsted Elnet", "5790000681105", "GEV-NT-01"),
    "hammel": ("Hammel Elforsyning Net", "5790001090166", "C-Tarif"),
    "hjerting": ("Hjerting Transformatorforening", "5790001095376", "C-Tarif"),
    "ikast": ("Ikast El Net", "5790000682102", "IEV-NT-01"),
    "konstant": ("Konstant", "5790000704842", "C_FBTNTR_B"),
    "l_net": ("L-Net", "5790001090111", "3000"),
    "laeso": ("Læsø Elnet", "5790001103460", "43100"),
    "midtfyns": ("Midtfyns Elforsyning", "5790001089023", "TNT15001"),
    "n1": ("N1", "5790001089030", "CD"),
    "noe": ("NOE Net", "5790000395620", "30030"),
    "nord_energi": ("Nord Energi Net", "5790000610877", "TAC"),
    "elvaerk": ("Netselskabet Elværk", "5790000681358", "5NCFF"),
    "radius": ("Radius", "5790000705689", "DT_C_01"),
    "rah": ("RAH Net", "5790000681327", "RAH-C"),
    "ravdex": ("Ravdex", "5790000836727", "NT-C"),
    "sunds": ("Sunds Net", "5790001095444", "SEF-NT-05"),
    "tarm": ("Tarm Elværk Net", "5790000706419", "TEV-NT-01T"),
    "trefor": ("TREFOR El-net", "5790000392261", "C"),
    "trefor_oest": ("TREFOR El-net Øst", "5790000706686", "46"),
    "veksel": ("Veksel", "5790001088217", "NT-01"),
    "vores_elnet": ("Vores Elnet", "5790000610976", "TNT1009"),
    "zeanet": ("Zeanet", "5790001089375", "43110"),
}


@dataclass
class Row:
    start: datetime                 # lokal dansk tid (uden tidszone), som datasættet angiver
    end: datetime | None
    prices: list[float | None]      # Price1..Price24 (kr/kWh uden moms), Price1 = kl. 00–01


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return None


def parse_rows(records: list[dict]) -> dict[str, list[Row]]:
    """Gruppér rækkerne pr. tarifkode, nyeste først."""
    out: dict[str, list[Row]] = {}
    for r in records or []:
        start = _parse_dt(r.get("ValidFrom"))
        if start is None:
            continue
        prices = []
        for i in range(1, 25):
            v = r.get(f"Price{i}")
            prices.append(float(v) if isinstance(v, (int, float)) else None)
        if all(p is None for p in prices):
            continue
        out.setdefault(str(r.get("ChargeTypeCode")), []).append(Row(start, _parse_dt(r.get("ValidTo")), prices))
    for rows in out.values():
        rows.sort(key=lambda x: x.start, reverse=True)
    return out


def price_at(rows: list[Row] | None, when_local: datetime) -> float | None:
    """Prisen i timen `when_local` (lokal tid). Har rækken kun Price1, gælder den hele døgnet."""
    t = when_local.replace(tzinfo=None)
    for row in rows or []:
        if row.start <= t and (row.end is None or t < row.end):
            p = row.prices[t.hour] if t.hour < len(row.prices) else None
            return p if p is not None else row.prices[0]
    return None


@dataclass
class Tariffs:
    """Hentede tariffer. Hvert opslag giver None, hvis der ikke er en gyldig pris – så bruges de indtastede."""
    tz: tzinfo
    grid: list[Row] = field(default_factory=list)
    energinet: dict[str, list[Row]] = field(default_factory=dict)

    def _local(self, when: datetime) -> datetime:
        return when.astimezone(self.tz) if when.tzinfo else when

    def net(self, when: datetime) -> float | None:
        return price_at(self.grid, self._local(when))

    def transmission(self, when: datetime) -> float | None:
        vals = [price_at(self.energinet.get(code), self._local(when)) for code in ENERGINET_CODES]
        return round(sum(vals), 6) if all(v is not None for v in vals) else None

    def elafgift(self, when: datetime) -> float | None:
        return price_at(self.energinet.get(ELAFGIFT_CODE), self._local(when))

    @property
    def has_data(self) -> bool:
        return bool(self.grid) or bool(self.energinet)

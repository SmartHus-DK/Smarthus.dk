"""Regulering efter solcelleoverskud (ingen Home Assistant-afhængigheder, så den kan testes).

Køres hver 30. sekund. `export_kw` er eksport til nettet (negativ = køb fra nettet).
"""

from __future__ import annotations

from dataclasses import dataclass

IMPORT_LIMIT_KW = 0.3     # køber vi mere end dette fra nettet, skrues ned
EXPORT_MARGIN_KW = 0.5    # eksporterer vi mere end dette, skrues op
START_TICKS = 2           # betingelsen for at starte skal holde i 2 × 30 s
ADJUST_TICKS = 2          # op/ned først når det har holdt i 2 × 30 s – ingen vippen op og ned hvert halve minut
SUN_GONE_KW = 0.2         # solen er væk, når solcellerne giver mindre end dette
STOP_TICKS = 10           # stop efter 5 minutter uden sol
STOP_TICKS_NO_SENSOR = 20 # uden solsensor: stop efter 10 minutter med køb på laveste strøm


@dataclass
class EvSolarState:
    """Tilstand for solcelleladning af én elbil."""

    active: bool = False
    current: int | None = None
    start_count: int = 0
    stop_count: int = 0
    up_count: int = 0
    down_count: int = 0

    def reset(self) -> None:
        self.active, self.current, self.start_count, self.stop_count = False, None, 0, 0
        self.up_count = self.down_count = 0


def regulate_ev(state: EvSolarState, export_kw: float, solar_kw: float | None,
                min_a: int, max_a: int, start_kw: float) -> EvSolarState:
    """Juster ladestrømmen 1 A ad gangen efter overskuddet: ned ved køb fra nettet, op ved eksport.

    * Der justeres først, når køb/eksport har holdt i 2 × 30 s, så strømmen ikke vipper op og ned.
    * Rækker overskuddet ikke til laveste strøm, bliver laderen tændt på laveste strøm (et kortvarigt
      køb er bedre end at slukke og tænde laderen hele tiden).
    * Laderen stoppes først, når solen er helt væk i 5 minutter (uden solsensor: 10 minutters køb på
      laveste strøm) – eller når solcelleladningen slås fra.
    """
    min_a, max_a = int(min_a), max(int(max_a), int(min_a))

    if not state.active:
        # Start, når der er nok sol (eller nok eksport, hvis der ikke er en solsensor)
        start = (solar_kw >= start_kw) if solar_kw is not None else (export_kw >= start_kw)
        state.start_count = state.start_count + 1 if start else 0
        if state.start_count >= START_TICKS:
            state.active, state.current, state.start_count, state.stop_count = True, min_a, 0, 0
            state.up_count = state.down_count = 0
        return state

    cur = state.current if state.current is not None else min_a
    buying = export_kw < -IMPORT_LIMIT_KW
    surplus = export_kw > EXPORT_MARGIN_KW
    state.down_count = state.down_count + 1 if buying and cur > min_a else 0
    state.up_count = state.up_count + 1 if surplus and cur < max_a else 0
    if state.down_count >= ADJUST_TICKS:
        cur, state.down_count = cur - 1, 0
    elif state.up_count >= ADJUST_TICKS:
        cur, state.up_count = cur + 1, 0
    state.current = cur

    # Stop kun, når solen er helt væk
    if solar_kw is not None:
        gone, limit = solar_kw < SUN_GONE_KW, STOP_TICKS
    else:
        gone, limit = buying and cur <= min_a, STOP_TICKS_NO_SENSOR
    state.stop_count = state.stop_count + 1 if gone else 0
    if state.stop_count >= limit:
        state.reset()
    return state

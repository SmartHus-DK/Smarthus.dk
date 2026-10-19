"""Regulering efter solcelleoverskud (ingen Home Assistant-afhængigheder, så den kan testes).

Køres hver 30. sekund. `export_kw` er eksport til nettet (negativ = køb fra nettet).
"""

from __future__ import annotations

from dataclasses import dataclass

IMPORT_LIMIT_KW = 0.3     # køber vi mere end dette fra nettet, skrues ned
EXPORT_MARGIN_KW = 0.5    # eksporterer vi mere end dette, skrues op
START_TICKS = 2           # betingelsen for at starte skal holde i 2 × 30 s
STOP_TICKS = 6            # stop efter 3 minutter uden overskud


@dataclass
class EvSolarState:
    """Tilstand for solcelleladning af én elbil."""

    active: bool = False
    current: int | None = None
    start_count: int = 0
    stop_count: int = 0

    def reset(self) -> None:
        self.active, self.current, self.start_count, self.stop_count = False, None, 0, 0


def regulate_ev(state: EvSolarState, export_kw: float, solar_kw: float | None,
                min_a: int, max_a: int, start_kw: float) -> EvSolarState:
    """Juster ladestrømmen 1 A ad gangen efter overskuddet – samme princip som en klassisk
    Easee-automatisering: ned ved køb fra nettet, op ved eksport (og nok sol), ellers hold.
    """
    min_a, max_a = int(min_a), max(int(max_a), int(min_a))
    sun_ok = solar_kw is None or solar_kw >= start_kw

    if not state.active:
        # Start, når der er nok sol (eller nok eksport, hvis der ikke er en solsensor)
        start = (solar_kw >= start_kw) if solar_kw is not None else (export_kw >= start_kw)
        state.start_count = state.start_count + 1 if start else 0
        if state.start_count >= START_TICKS:
            state.active, state.current, state.start_count, state.stop_count = True, min_a, 0, 0
        return state

    cur = state.current if state.current is not None else min_a
    if export_kw < -IMPORT_LIMIT_KW:
        if cur > min_a:
            cur -= 1
            state.stop_count = 0
        else:
            state.stop_count += 1       # på laveste strøm og køber stadig
    elif export_kw > EXPORT_MARGIN_KW and sun_ok:
        cur = min(cur + 1, max_a)
        state.stop_count = 0
    elif solar_kw is not None and solar_kw < start_kw * 0.5:
        state.stop_count += 1           # solen er væk
    else:
        state.stop_count = 0
    state.current = cur
    if state.stop_count >= STOP_TICKS:
        state.reset()
    return state

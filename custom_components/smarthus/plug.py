"""Hvilken bil sidder i laderen? (ingen Home Assistant-afhængigheder, så den kan testes)."""

from __future__ import annotations

NONE = "none"   # manuelt valg: ingen bil i laderen
_UNKNOWN = {"unknown", "unavailable", "none", ""}
_NO = {"off", "false", "no", "0", "disconnected", "unplugged", "not_connected", "notconnected",
       "not connected", "not_plugged", "available", "idle_disconnected",
       "idle", "no car", "no_car", "no_ev_connected", "search_comm"}   # go-e/Wattpilot: Idle = ingen bil


def plug_state(state: str | None, off: tuple | list = ()) -> bool | None:
    """Fortolk en 'ladekabel tilsluttet'-sensor. True/False, eller None hvis ukendt.
    `off` er ekstra tilstande, der betyder "ingen bil" for netop denne lader (fx Wallbox' "Ready")."""
    if state is None:
        return None
    s = str(state).strip().lower()
    if s in _UNKNOWN:
        return None
    if s in {str(x).lower() for x in off or ()}:
        return False
    if s in _NO or "disconnect" in s or "unplug" in s or "not connected" in s or "not_connected" in s:
        return False
    if s in {"on", "true", "yes", "1"} or any(w in s for w in ("connect", "plug", "charg", "complete",
                                                                  "ready", "awaiting", "paused", "finished", "wait", "preparing",
                                                                  "suspended", "finishing", "occupied")):
        return True
    return None


def pick_connected(car_ids: list[str], plug: dict[str, bool | None], manual: str | None) -> str | None:
    """Vælg den tilsluttede bil blandt biler, der deler én lader.

    1) Biler hvis sensor siger tilsluttet (det manuelle valg foretrækkes, hvis flere).
    2) Ellers biler uden sensor/ukendt status – det manuelle valg. Er der kun én bil tilbage,
       fordi de andre melder 'ikke tilsluttet', er det den.
    3) Ellers er ingen bil tilsluttet – brugeren skal vælge bilen (manual = None eller "none").
    """
    yes = [c for c in car_ids if plug.get(c) is True]
    if yes:
        return manual if manual in yes else yes[0]
    unknown = [c for c in car_ids if plug.get(c) is None]
    if manual in unknown:
        return manual
    if len(unknown) == 1 and len(unknown) < len(car_ids) and manual != NONE:
        return unknown[0]
    return None

"""Hvilken bil sidder i laderen? (ingen Home Assistant-afhængigheder, så den kan testes)."""

from __future__ import annotations

_UNKNOWN = {"unknown", "unavailable", "none", ""}
_NO = {"off", "false", "no", "0", "disconnected", "unplugged", "not_connected", "notconnected",
       "not connected", "not_plugged", "available", "idle_disconnected"}


def plug_state(state: str | None) -> bool | None:
    """Fortolk en 'ladekabel tilsluttet'-sensor. True/False, eller None hvis ukendt."""
    if state is None:
        return None
    s = str(state).strip().lower()
    if s in _UNKNOWN:
        return None
    if s in _NO or "disconnect" in s or "unplug" in s or "not connected" in s or "not_connected" in s:
        return False
    if s in {"on", "true", "yes", "1"} or any(w in s for w in ("connect", "plug", "charg", "complete",
                                                                  "ready", "awaiting", "paused", "finished")):
        return True
    return None


def pick_connected(car_ids: list[str], plug: dict[str, bool | None], manual: str | None) -> str | None:
    """Vælg den tilsluttede bil blandt biler, der deler én lader.

    1) Biler hvis sensor siger tilsluttet (det manuelle valg foretrækkes, hvis flere).
    2) Ellers biler uden sensor/ukendt status – det manuelle valg, ellers den første.
    3) Siger alle sensorer 'ikke tilsluttet', er ingen bil tilsluttet.
    """
    yes = [c for c in car_ids if plug.get(c) is True]
    if yes:
        return manual if manual in yes else yes[0]
    unknown = [c for c in car_ids if plug.get(c) is None]
    if unknown:
        return manual if manual in unknown else unknown[0]
    return None

"""Hurtigere opdateringer i HACS.

HACS tjekker kun brugerdefinerede repositories ved opstart og derefter hver 48. time. SmartHus
tjekker derfor selv GitHub for en ny udgivelse (lidt efter opstart og derefter hver 3. time), og
findes der en, bedes HACS om at opdatere sine oplysninger om SmartHus – så opdateringen dukker op
med det samme under Indstillinger → Opdateringer.

Alt her er "best effort": mangler HACS, eller ændrer HACS sin interne API, sker der bare ingenting.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REPO = "SmartHus-DK/Smarthus.dk"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
FIRST_CHECK_SEC = 300
CHECK_INTERVAL = timedelta(hours=3)


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """'v1.35.1' → (1, 35, 1). Ukendt format → None."""
    if not v:
        return None
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(v))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str | None, current: str | None) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


async def _latest_release(hass: HomeAssistant) -> str | None:
    session = async_get_clientsession(hass)
    async with session.get(
        RELEASE_URL, headers={"Accept": "application/vnd.github+json"}, timeout=30
    ) as resp:
        if resp.status != 200:
            _LOGGER.debug("GitHub svarede %s ved opdateringstjek", resp.status)
            return None
        data = await resp.json()
    return data.get("tag_name")


async def _refresh_hacs(hass: HomeAssistant, latest: str) -> bool:
    hacs = hass.data.get("hacs")
    repos = getattr(hacs, "repositories", None)
    if repos is None:
        return False
    repo = repos.get_by_full_name(REPO)
    if repo is None:
        return False
    if not is_newer(latest, repo.display_available_version):
        return False  # HACS kender allerede den nye version
    _LOGGER.info("Ny SmartHus-version %s fundet – beder HACS om at opdatere sine oplysninger", latest)
    await repo.update_repository(ignore_issues=True, force=True)
    await hacs.data.async_write()
    coordinator = hacs.coordinators.get(repo.data.category)
    if coordinator is not None:
        coordinator.async_update_listeners()
    return True


async def async_check(hass: HomeAssistant) -> None:
    try:
        current = (await async_get_integration(hass, DOMAIN)).version
        latest = await _latest_release(hass)
        if latest and is_newer(latest, str(current)):
            await _refresh_hacs(hass, latest)
    except Exception as err:  # noqa: BLE001 – må aldrig påvirke resten af SmartHus
        _LOGGER.debug("Opdateringstjek fejlede: %s", err)


@callback
def async_setup_update_check(hass: HomeAssistant) -> None:
    """Start det periodiske tjek (kaldes én gang fra async_setup)."""

    @callback
    def _run(_now=None) -> None:
        hass.async_create_background_task(async_check(hass), "smarthus_update_check")

    async_call_later(hass, FIRST_CHECK_SEC, _run)
    async_track_time_interval(hass, _run, CHECK_INTERVAL, cancel_on_shutdown=True)

"""Test af sne og rim på solcellerne (kører uden Home Assistant)."""

import importlib.util
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PKG = Path(__file__).parents[1] / "custom_components" / "smarthus"
if "smarthus" not in sys.modules:
    pkg = types.ModuleType("smarthus")
    pkg.__path__ = [str(PKG)]
    sys.modules["smarthus"] = pkg
spec = importlib.util.spec_from_file_location("smarthus.cover", PKG / "cover.py")
cover = importlib.util.module_from_spec(spec)
sys.modules["smarthus.cover"] = cover
spec.loader.exec_module(cover)

TZ = ZoneInfo("Europe/Copenhagen")
D0 = datetime(2026, 11, 20, 0, 0, tzinfo=TZ)


def weather(temps, snowfall=None, depth=None, start=D0 - timedelta(hours=12)):
    """Vejr pr. time fra `start`: lister med temperatur, snefald (cm) og snedybde (m)."""
    n = len(temps)
    snowfall = snowfall or [0.0] * n
    depth = depth or [0.0] * n
    return {start + timedelta(hours=i): (temps[i], snowfall[i], depth[i]) for i in range(n)}


def at(hour, minute=0, day=0):
    return D0 + timedelta(days=day, hours=hour, minutes=minute)


def test_snow_covers_until_panels_produce():
    pc = cover.PanelCover()
    n = 48
    fall = [0.0] * n
    fall[10] = 2.0                                         # sne kl. 22 aftenen før
    pc.set_weather(weather([-1.0] * n, fall, [0.03] * n))
    assert pc.state(at(10)) == "snow" and pc.factor(at(10)) == cover.SNOW_FACTOR
    # Panelerne producerer kun lidt: stadig sne
    for m in range(0, 15):
        pc.observe(at(11, m), 0.1, 2.0, 60)
    assert pc.state(at(12)) == "snow"
    # Sneen er gledet af: over 40 % i 10 minutter
    for m in range(0, 12):
        pc.observe(at(12, m), 1.5, 2.0, 60)
    assert pc.data.get("proof") and pc.state(at(13)) != "snow"
    # Nyt snefald i morgen tidlig: dækket igen
    fall2 = list(fall)
    fall2[12 + 24 + 5] = 1.0
    pc.set_weather(weather([3.0] * (n + 24), fall2 + [0.0] * 24, [0.03] * (n + 24)))
    assert pc.state(at(9, day=1)) == "snow"


def test_old_snow_on_ground_after_proof_is_clear():
    pc = cover.PanelCover()
    pc.set_weather(weather([2.0] * 48, None, [0.05] * 48))  # sne på jorden, men intet nyt snefald
    assert pc.state(at(10)) == "snow"                     # ingen bevis endnu: regn med sne
    for m in range(0, 12):
        pc.observe(at(10, m), 2.0, 2.5, 60)
    assert pc.state(at(11)) is None and pc.state(at(11, day=1)) is None


def test_frost_morning_and_learning():
    pc = cover.PanelCover()
    temps = [1.0] * 12 + [-0.6, -1, -1, -1, -1, -1, -0.8, -0.6, 0.1, 0.3, 0.5, 1.2, 2.0, 2.0] + [3.0] * 10
    pc.set_weather(weather(temps))
    assert pc.state(at(9)) == "frost" and pc.state(at(10)) == "frost"
    assert pc.state(at(11)) is None                       # 1,2 °C: rimen er smeltet
    assert pc.factor(at(9)) == cover.FROST_START
    # Formiddagen giver kun en fjerdedel: andelen lærer det
    for m in range(0, 120, 1):
        t = at(8) + timedelta(minutes=m)
        pc.observe(t, 0.25, 1.0, 60)
    pc.learn(at(0, 25, day=1))
    assert abs(pc.data["frost_ratio"] - (0.6 + 0.3 * (0.25 - 0.6))) < 0.01
    assert pc.data["frost_days"] == 1


def test_frost_cleared_when_panels_produce():
    pc = cover.PanelCover()
    temps = [-2.0] * 30
    pc.set_weather(weather(temps))
    assert pc.state(at(10)) == "frost"
    for m in range(0, 12):
        pc.observe(at(10, m), 2.0, 2.2, 60)
    assert pc.state(at(11)) is None                       # rene resten af dagen


def test_no_frost_without_cold_night():
    pc = cover.PanelCover()
    pc.set_weather(weather([2.0] * 20 + [0.4] * 10))     # kold formiddag, men ingen frost i natten
    assert pc.state(at(9)) is None


def test_summary():
    pc = cover.PanelCover()
    temps = [-2.0] * 22 + [3.0] * 10
    pc.set_weather(weather(temps))
    hours = [at(h) for h in range(8, 16)]
    s = pc.summary(hours, at(6))
    assert s and s["state"] == "frost" and s["from"] == at(8).isoformat() and s["until"] == at(10).isoformat()

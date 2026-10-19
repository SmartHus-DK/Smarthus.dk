"""Test af batteri-/eksportstyring i controlleren med en lille attrap af Home Assistant."""
import asyncio, sys, types, importlib.util
from pathlib import Path
from datetime import timezone

PKG = Path(__file__).parents[1] / "custom_components" / "smarthus"

def stub(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m

class _Any:
    def __init__(self, *a, **k): pass
    def __class_getitem__(cls, item): return cls
stub("homeassistant"); stub("homeassistant.config_entries", ConfigEntry=_Any)
stub("homeassistant.const", STATE_ON="on", STATE_UNAVAILABLE="unavailable", STATE_UNKNOWN="unknown")
stub("homeassistant.core", HomeAssistant=_Any, callback=lambda f: f)
stub("homeassistant.helpers")
stub("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None)
stub("homeassistant.helpers.event", async_call_later=lambda *a, **k: None,
     async_track_state_change_event=lambda *a, **k: None, async_track_time_change=lambda *a, **k: None)
stub("homeassistant.helpers.storage", Store=_Any)
stub("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=_Any, UpdateFailed=Exception)
stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda h: None)
import datetime as _dt
from zoneinfo import ZoneInfo
dtu = types.SimpleNamespace(get_default_time_zone=lambda: ZoneInfo("Europe/Copenhagen"),
                            utcnow=lambda: _dt.datetime.now(timezone.utc), now=lambda: _dt.datetime.now(ZoneInfo("Europe/Copenhagen")))
stub("homeassistant.util", dt=dtu)
stub("aiohttp", ClientError=Exception)

pkg = types.ModuleType("smarthus"); pkg.__path__ = [str(PKG)]; sys.modules["smarthus"] = pkg
def load(n):
    spec = importlib.util.spec_from_file_location(f"smarthus.{n}", PKG / f"{n}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[f"smarthus.{n}"] = m; spec.loader.exec_module(m); return m
for n in ("const", "planner", "pricing", "savings", "profile", "coordinator", "controller"):
    load(n)
ctl_mod = sys.modules["smarthus.controller"]; planner = sys.modules["smarthus.planner"]

class State:
    def __init__(self, state, **attrs): self.state, self.attributes = state, attrs
class Services:
    def __init__(self): self.calls = []
    async def async_call(self, domain, service, data, blocking=False): self.calls.append((domain, service, data))
class Hass:
    def __init__(self, states): self.services = Services(); self.states = types.SimpleNamespace(get=states.get)

def make(config, states):
    hass = Hass(states)
    c = ctl_mod.SmartHusController.__new__(ctl_mod.SmartHusController)
    c.hass, c.config = hass, config
    c.settings = dict(sys.modules["smarthus.const"].DEFAULT_SETTINGS)
    c._last_cmd, c._script_state = {}, {}
    c.plan = planner.Plan()
    return c, hass

async def main():
    # 1) Batteri via scripts: kun kald ved skift
    c, h = make({"battery_script_charge": "script.lad", "battery_script_discharge": "script.aflad",
                 "battery_script_normal": "script.normal"}, {})
    assert c.has_battery_control and c.has("battery_mode_entity")
    await c._apply_battery("charge", False); await c._apply_battery("charge", False)
    assert [x[2]["entity_id"] for x in h.services.calls] == ["script.lad"], h.services.calls
    assert h.services.calls[0][2]["variables"]["smarthus_state"] == "charge"
    await c._apply_battery("normal", False)
    assert h.services.calls[-1][2]["entity_id"] == "script.normal"
    # 2) Script for én tilstand, rullemenu for resten
    c, h = make({"battery_mode_entity": "select.mode", "battery_script_charge": "script.lad",
                 "battery_option_normal": "Self Use"},
                {"select.mode": State("Force Charge", options=["Self Use", "Force Charge"])})
    await c._apply_battery("normal", False)
    assert h.services.calls == [("select", "select_option", {"entity_id": "select.mode", "option": "Self Use"})]
    # 3) Eksport via rullemenu
    c, h = make({"solar_curtail_entity": "select.export", "export_option_stop": "Zero Export",
                 "export_option_normal": "Selling First"},
                {"select.export": State("Selling First", options=["Selling First", "Zero Export"])})
    assert c.has_export_control
    await c._apply_export(True, False)
    assert h.services.calls[-1] == ("select", "select_option", {"entity_id": "select.export", "option": "Zero Export"})
    # 4) Eksport via tal (eksportgrænse)
    c, h = make({"solar_curtail_entity": "number.export_limit", "export_value_stop": 0, "export_value_normal": 10000},
                {"number.export_limit": State("10000")})
    await c._apply_export(True, False)
    assert h.services.calls[-1] == ("number", "set_value", {"entity_id": "number.export_limit", "value": 0.0})
    # 5) Eksport via kontakt (som før)
    c, h = make({"solar_curtail_entity": "switch.zero"}, {"switch.zero": State("off")})
    await c._apply_export(True, False)
    assert h.services.calls[-1] == ("switch", "turn_on", {"entity_id": "switch.zero"})
    # 6) Eksport via scripts
    c, h = make({"export_script_stop": "script.stop", "export_script_start": "script.start"}, {})
    assert c.has_export_control
    await c._apply_export(True, False); await c._apply_export(False, False)
    assert [x[2]["entity_id"] for x in h.services.calls] == ["script.stop", "script.start"]
    # 7) Intet sat op
    c, h = make({}, {})
    assert not c.has_battery_control and not c.has_export_control
    print("controller OK")

def test_controller_control():
    asyncio.run(main())

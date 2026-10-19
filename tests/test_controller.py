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
for n in ("const", "planner", "pricing", "savings", "profile", "solar", "plug", "coordinator", "controller"):
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
    c.hotwater_full, c.hotwater_surplus, c._surplus_count = False, False, 0
    c.heating_outdoor_ok = True
    c._hw_surplus_sec, c._hw_done_date = 0.0, None
    c.flags = {"hotwater_surplus_smart": True}
    c.ev_solar, c._ev_current_sent = {}, {}
    c.tz = ZoneInfo("Europe/Copenhagen")
    c.enabled = {"master": True}
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
    # 8) Varmt vand med temperaturføler: sluk ved grænsen, tænd igen efter 3 °C afkøling
    temp = {"v": "50"}
    states = {"sensor.vand": types.SimpleNamespace(state=None, attributes={})}
    c, h = make({"hotwater_entity": "switch.vand", "hotwater_temp_sensor": "sensor.vand"}, {})
    c.hass.states = types.SimpleNamespace(get=lambda e: State(temp["v"]) if e == "sensor.vand" else None)
    c.settings["hotwater_boost_temp"] = 55.0
    assert c._hotwater_switch_on(True) is True       # 50 °C: varm op
    temp["v"] = "55.5"
    assert c._hotwater_switch_on(True) is False      # grænsen nået
    temp["v"] = "53"
    assert c._hotwater_switch_on(True) is False      # stadig varmt nok (hysterese)
    temp["v"] = "51.5"
    assert c._hotwater_switch_on(True) is True       # kølet > 3 °C af: varm igen
    assert c._hotwater_switch_on(False) is False     # uden for billig periode: slukket
    # Uden føler: følg planen
    c, h = make({"hotwater_entity": "switch.vand"}, {})
    assert c._hotwater_switch_on(True) is True
    # 9) Solcelleoverskud: start ved eksport >= grænse i 2 min, stop ved køb fra nettet i 2 min
    grid = {"v": "500", "unit": "W"}
    c, h = make({"hotwater_entity": "switch.vand", "grid_power_sensor": "sensor.grid",
                 "grid_power_sign": "export_positive"}, {})
    c.hass.states = types.SimpleNamespace(get=lambda e: State(grid["v"], unit_of_measurement=grid["unit"]) if e == "sensor.grid" else None)
    c.settings["hotwater_surplus_kw"] = 2.0
    c.flags["hotwater_surplus_smart"] = False                     # "brug altid overskud"
    c._update_surplus(); assert not c.hotwater_surplus          # 0,5 kW eksport: under grænsen
    grid["v"] = "2500"
    for _ in range(3): c._update_surplus()
    assert not c.hotwater_surplus                               # 1,5 min over grænsen
    c._update_surplus(); assert c.hotwater_surplus              # 2 min: start
    assert c._hotwater_switch_on(False) is True                 # tænd selv uden billig time
    grid["v"] = "100"
    for _ in range(4): c._update_surplus()
    assert c.hotwater_surplus                                   # lav eksport: kør videre
    grid["v"] = "-800"
    for _ in range(3): c._update_surplus()
    assert c.hotwater_surplus
    c._update_surplus(); assert not c.hotwater_surplus          # køber 0,8 kW i 2 min: stop
    # Fortegn: positiv = køb fra nettet, enhed kW
    grid.update(v="-3", unit="kW")
    c.config["grid_power_sign"] = "import_positive"
    assert abs(c.export_kw - 3.0) < 1e-9
    c.settings["hotwater_surplus_kw"] = 0
    c._update_surplus(); assert not c.hotwater_surplus          # 0 = slået fra
    # 10) "Kun når det betaler sig": vent, hvis salgsprisen er højere end den billige time
    from datetime import timedelta as _td
    now = _dt.datetime.now(timezone.utc)
    S = planner.Slot
    cur = S(now - _td(minutes=5), now + _td(minutes=10), 1.0, 2.5, 1.2)    # sælg nu for 1,20 kr
    later = S(now + _td(hours=3), now + _td(hours=4), 0.2, 0.9, 0.2)      # billig time 0,90 kr
    grid.update(v="3000", unit="W"); c.config["grid_power_sign"] = "export_positive"
    c.settings["hotwater_surplus_kw"] = 2.0
    c.flags["hotwater_surplus_smart"] = True
    c.hotwater_surplus, c._surplus_count, c._hw_done_date = False, 0, None
    c.plan = planner.Plan(current=cur, hotwater_slots=[later])
    assert not c.surplus_worth_it()
    for _ in range(5): c._update_surplus()
    assert not c.hotwater_surplus                               # sælger hellere
    c.plan = planner.Plan(current=S(cur.start, cur.end, 0.1, 1.5, 0.05), hotwater_slots=[later])
    assert c.surplus_worth_it()                                 # salg giver kun 0,05 kr
    for _ in range(4): c._update_surplus()
    assert c.hotwater_surplus
    # 11) Kun én opvarmning om dagen: når timerne er brugt, er vandet varmet i dag
    c.settings["hotwater_hours"] = 0.05                         # 3 minutter
    for _ in range(6): c._update_surplus()
    assert not c.hotwater_surplus and c.hotwater_heated_today
    for _ in range(6): c._update_surplus()
    assert not c.hotwater_surplus                               # ikke igen i dag
    c.plan = planner.Plan(current=S(now - _td(minutes=5), now + _td(minutes=10), 0.1, 0.9, 0.05),
                          hotwater_slots=[S(now - _td(minutes=5), now + _td(minutes=10), 0.1, 0.9, 0.05)])
    assert c._hotwater_switch_on(True) is False                 # dagens billige time springes over
    # 12) Elbil med sol: fuld strøm i billig time, ellers regulering
    car = {"id": "ev", "name": "Kia", "charger": "switch.lader", "current_entity": "number.strom"}
    c.cars = [car]
    c.flags["ev_solar"] = True
    ep = planner.EvPlan("ev", "Kia", on=True)
    assert c._ev_target(car, ep, None) == (True, 16)            # billig time: 16 A
    ep = planner.EvPlan("ev", "Kia", on=False)
    c.plan = planner.Plan(current=cur)
    grid.update(v="5000")
    assert c._ev_target(car, ep, None) == (False, None)         # 1. måling
    assert c._ev_target(car, ep, None) == (True, 6)             # starter på 6 A (ingen solsensor: eksport >= 4 kW)
    assert c._ev_target(car, ep, None) == (True, 7)             # eksport: op
    assert c._ev_target(car, ep, "ev_tesla") == (False, None)   # en anden bil lader med sol
    c.flags["ev_solar"] = False
    assert c._ev_target(car, ep, None) == (False, None)         # slået fra: følg planen
    # 13) Huawei uden scripts: rigtige handlinger, kun ved skift
    c, h = make({"battery_huawei_device": "dev123"}, {})
    c.settings.update(battery_max_soc=95, battery_min_soc=15)
    assert c.has_battery_control
    await c._apply_battery("charge", False); await c._apply_battery("charge", False)
    assert h.services.calls == [("huawei_solar", "forcible_charge_soc",
                                 {"device_id": "dev123", "target_soc": 95, "power": 5000})]
    await c._apply_battery("discharge", False)
    assert h.services.calls[-1] == ("huawei_solar", "forcible_discharge_soc",
                                    {"device_id": "dev123", "target_soc": 15, "power": 5000})
    await c._apply_battery("normal", False)
    assert h.services.calls[-1] == ("huawei_solar", "stop_forcible_charge", {"device_id": "dev123"})
    # 14) Easee uden script
    c, h = make({}, {})
    car = {"id": "ev", "name": "Kia", "charger": "switch.lader", "easee_device": "easee1"}
    assert c.has_current_control(car)
    await c._apply_current(car, 9, False); await c._apply_current(car, 9, False)
    assert h.services.calls == [("easee", "set_charger_dynamic_limit",
                                 {"device_id": "easee1", "current": 9, "time_to_live": 0})]
    # 15) To biler på én lader: kun den tilsluttede styrer laderen
    plugs = {"binary_sensor.kia_plug": "off", "binary_sensor.tesla_plug": "on"}
    c, h = make({}, {})
    c.hass.states = types.SimpleNamespace(get=lambda e: State(plugs[e]) if e in plugs else None)
    c.cars = [{"id": "ev", "name": "Kia", "charger": "switch.easee", "plug": "binary_sensor.kia_plug"},
              {"id": "ev_tesla", "name": "Tesla", "charger": "switch.easee", "plug": "binary_sensor.tesla_plug"}]
    c.car = lambda cid: next(x for x in c.cars if x["id"] == cid)
    c.shared_chargers = {"switch.easee": ["ev", "ev_tesla"]}
    c.connected, c.connected_source, c.connected_choice = {}, {}, {}
    c._update_connected()
    assert c.connected == {"ev": False, "ev_tesla": True} and c.connected_source["ev"] == "sensor"
    plugs.update({"binary_sensor.kia_plug": "unavailable", "binary_sensor.tesla_plug": "unavailable"})
    c.connected_choice["switch.easee"] = "ev"
    c._update_connected()
    assert c.connected == {"ev": True, "ev_tesla": False}            # ukendt: manuelt valg
    # 16) Varme: kun aktiv under udetemperaturgrænsen (1 °C hysterese)
    out = {"v": "8"}
    c, h = make({"heating_climate_entity": "climate.vp", "outdoor_temp_sensor": "sensor.ude"}, {})
    c.hass.states = types.SimpleNamespace(get=lambda e: State(out["v"]) if e == "sensor.ude" else None)
    assert c.settings["heating_max_outdoor"] == 10.0
    assert c._update_heating_outdoor() is True        # 8 °C: aktiv
    out["v"] = "10.2"; assert c._update_heating_outdoor() is False   # over grænsen: pause
    out["v"] = "9.5"; assert c._update_heating_outdoor() is False    # inden for hysteresen
    out["v"] = "unavailable"; assert c._update_heating_outdoor() is False  # føler væk: behold
    out["v"] = "8.9"; assert c._update_heating_outdoor() is True     # 1 °C under: aktiv igen
    c, h = make({"heating_climate_entity": "climate.vp"}, {})
    assert c._update_heating_outdoor() is True        # uden udeføler: altid aktiv
    print("controller OK")

def test_controller_control():
    asyncio.run(main())

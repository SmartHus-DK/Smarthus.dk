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
for n in ("const", "planner", "pricing", "savings", "profile", "solar", "plug", "flow", "weather", "suncal", "coordinator", "controller"):
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
    c._heating_paused_applied = False
    c._ev_charged = {}
    c.ev_ready_mode = {}
    c._hw_surplus_sec, c._hw_done_date = 0.0, None
    c.hw_demand_now, c.hw_demand, c.hw_deadline, c.hw_rate, c._hw_samples = False, [], None, None, []
    c.heating_too_cold, c._heating_off_by_us = False, False
    c.flags = {"hotwater_surplus_smart": True}
    c._forecast_wh, c._forecast_key_is_end = {}, False
    c.ev_solar, c._ev_current_sent = {}, {}
    c._ev_hold, c._restore_pending, c._ev_stray, c._soc_est = {}, {}, {}, {}
    c.connected = {}
    c._ev_past, c._ev_manual = {}, {}
    c.manual_soc, c._ev_idle, c._ev_full_key = {}, {}, {}
    c.tz = ZoneInfo("Europe/Copenhagen")
    c.enabled = {"master": True}
    c.plan = planner.Plan()
    c.sun = sys.modules["smarthus.weather"].SunTracker()
    c.solar_cal = sys.modules["smarthus.suncal"].SolarCalibration()
    c._forecast_domain = None
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
    # Allerede varmet op i dag: ingen ny almindelig opvarmning – men "efter behov" må gerne
    from datetime import datetime as _hdt, timedelta as _htd, timezone as _htz
    n = _hdt.now(_htz.utc)
    c.plan.current = planner.Slot(n - _htd(minutes=5), n + _htd(minutes=10), 0.5, 1.0, 0.4)
    c._hw_done_date = c._today()
    temp["v"] = "43"
    assert c._hotwater_switch_on(True) is False
    c.hw_demand_now = True
    assert c._hotwater_switch_on(True) is True
    # Vandets temperatur fra en water_heater (current_temperature) – valgt eller som styret entitet
    wh = {"water_heater.dhw1": State("eco", current_temperature=48, temperature=42)}
    c, h = make({"hotwater_entity": "button.charge", "hotwater_temp_sensor": "water_heater.dhw1",
                 "hotwater_min_temp_entity": "water_heater.dhw1"}, wh)
    assert c.hotwater_temp == 48.0 and c.hotwater_min_temp == 42.0
    c, h = make({"hotwater_entity": "water_heater.dhw1"}, wh)
    assert c.hotwater_temp_entity == "water_heater.dhw1" and c.hotwater_temp == 48.0
    c, h = make({"hotwater_entity": "switch.vand"}, {})
    assert c.hotwater_temp_entity is None
    # 0 eller tekst er ikke en vandtemperatur
    c, h = make({"hotwater_entity": "button.charge", "hotwater_temp_sensor": "sensor.dhw1",
                 "hotwater_min_temp_entity": "sensor.dhw1"}, {"sensor.dhw1": State("0")})
    assert c.hotwater_temp is None and c.hotwater_min_temp == 42.0
    c, h = make({"hotwater_entity": "button.charge", "hotwater_temp_sensor": "sensor.dhw1",
                 "hotwater_min_temp_entity": "sensor.dhw1"},
                {"sensor.dhw1": State("eco", current_temperature=48, temperature=42)})
    assert c.hotwater_temp == 48.0 and c.hotwater_min_temp == 42.0
    # Laveste temperatur: varmepumpens eget setpunkt, ellers indstillingen
    c, h = make({"hotwater_min_temp_entity": "number.dhw_min"}, {"number.dhw_min": State("41.5")})
    assert c.hotwater_min_temp == 41.5
    c, h = make({"hotwater_min_temp_entity": "water_heater.dhw1"},
                {"water_heater.dhw1": State("heat_pump", temperature=43)})
    assert c.hotwater_min_temp == 43.0
    c, h = make({"hotwater_min_temp_entity": "number.dhw_min"}, {"number.dhw_min": State("unavailable")})
    assert c.hotwater_min_temp == 42.0 and c.hotwater_min_source is None
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
    assert c._ev_target(car, ep, None) == (True, 6)             # eksport: vent 2 × 30 s
    assert c._ev_target(car, ep, None) == (True, 7)             # eksport holdt: op
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
    # 14b) Easee: efter sluk (og Easee's pause, der sætter strømmen til 0 A) sendes strømmen igen ved start
    c, h = make({}, {"switch.lader": State("on")})
    c.cars = [car]
    c.enabled = {"master": True, "ev": True}
    c.active = lambda m: True
    c._ev_target = lambda car_, ep, sc, step=True: (ep.on, 16 if ep.on else None)
    c.ev_on_hold = lambda cid: False
    async def _noop(*a, **k): return None
    c._set_charger = _noop; c._ev_verify_off = _noop
    on = types.SimpleNamespace(on=True, connected=True); off = types.SimpleNamespace(on=False, connected=True)
    await c._apply_ev_step(types.SimpleNamespace(evs={"ev": on}), False)
    await c._apply_ev_step(types.SimpleNamespace(evs={"ev": off}), False)
    await c._apply_ev_step(types.SimpleNamespace(evs={"ev": on}), False)
    lim = [x for x in h.services.calls if x[1] == "set_charger_dynamic_limit"]
    assert len(lim) == 2, lim
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
    assert c._update_heating_outdoor() is True        # uden udeføler og vejr: altid aktiv
    # Fallback til vejret fra skyen, når der ikke er en føler (eller den ikke svarer)
    wx = {"weather.forecast_home": State("rainy", temperature=11.0), "sensor.ude": State("unavailable"),
          "sun.sun": State("above_horizon")}
    c, h = make({"heating_climate_entity": "climate.vp", "outdoor_temp_sensor": "sensor.ude"}, {})
    c.hass.states = types.SimpleNamespace(get=wx.get,
        async_all=lambda d: [types.SimpleNamespace(entity_id=e) for e in wx if e.startswith(d + ".")])
    assert c.weather_entity == "weather.forecast_home"
    assert c.outdoor_temp == 11.0 and c.outdoor_source == "weather"
    assert c._update_heating_outdoor() is False       # 11 °C fra vejret: pause
    wx["sensor.ude"] = State("4.5")
    assert c.outdoor_temp == 4.5 and c.outdoor_source == "sensor"
    assert c._update_heating_outdoor() is True        # egen føler vinder
    info = c.weather_info
    assert info["condition"] == "rainy" and info["temperature"] == 4.5 and info["night"] is False
    # 17) Varmt vand via varmepumpens knap ("charge"): tryk én gang pr. periode
    c, h = make({"hotwater_entity": "button.vp_charge"}, {"button.vp_charge": State("unknown")})
    c._hw_button_on = False
    await c._apply_hotwater(True, False); await c._apply_hotwater(True, False)
    assert h.services.calls == [("button", "press", {"entity_id": "button.vp_charge"})], h.services.calls
    await c._apply_hotwater(False, False); await c._apply_hotwater(True, False)
    assert len(h.services.calls) == 2                 # ny periode: nyt tryk
    # 18) Sælg solstrøm i stedet for at lade batteriet, når det betaler sig og huset er dækket
    from datetime import datetime, timedelta
    prof = sys.modules["smarthus.profile"]
    now = datetime(2026, 6, 10, 10, 0, tzinfo=timezone.utc)
    mk = lambda h, price, sale: planner.Slot(now + timedelta(hours=h), now + timedelta(hours=h, minutes=15), 0, price, sale)
    c, h = make({"battery_mode_entity": "select.mode", "battery_option_normal": "Self Use",
                 "battery_option_sell_solar": "Feed-in First"},
                {"select.mode": State("Self Use", options=["Self Use", "Feed-in First"])})
    assert c.has_sell_solar and c.has("battery_sell_solar")
    c.plan.current = mk(0, 2.0, 1.40)
    c.plan.battery_charge = [mk(16, 0.80, 0.2)]
    c.reserve = prof.Reserve(reserve_kwh=4, sellable_kwh=3, battery_kwh=9)
    c.sell_solar = None
    c._decide_sell_solar(now)
    assert c.plan.battery_action == "sell_solar" and c.sell_solar["active"], c.sell_solar
    await c._apply_battery(c.plan.battery_action, False)
    assert h.services.calls[-1][2]["option"] == "Feed-in First"
    # Huset ikke dækket -> batteriet lades som normalt
    c.plan.battery_action = "normal"; c.reserve = prof.Reserve(reserve_kwh=6, sellable_kwh=0, battery_kwh=4)
    c._decide_sell_solar(now)
    assert c.plan.battery_action == "normal" and not c.sell_solar["active"]
    # For lav salgspris -> ingen salg
    c.plan.current = mk(0, 1.0, 0.50); c.reserve = prof.Reserve(reserve_kwh=4, sellable_kwh=3, battery_kwh=9)
    c._decide_sell_solar(now)
    assert c.plan.battery_action == "normal"
    # 19) Netopladning springes over, når solen forventes at fylde batteriet
    class C(ctl_mod.SmartHusController):
        fc = {}
        @property
        def solar_forecast(self): return self.fc
    night = datetime(2026, 6, 10, 0, 30, tzinfo=timezone.utc)   # 02:30 dansk tid
    c, h = make({"battery_mode_entity": "select.mode", "battery_soc_sensor": "sensor.soc"},
                {"sensor.soc": State("40")})
    c.__class__ = C
    c.coordinator = types.SimpleNamespace(data=[])
    c.profile = prof.build_profile([(datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(hours=i), 0.5) for i in range(7 * 24)])
    slot = planner.Slot(night, night + timedelta(minutes=15), 0, 0.8, 0.2)
    c.plan.current = slot; c.plan.battery_charge = [slot]; c.plan.battery_action = "charge"
    c.settings["battery_capacity"] = 10
    C.fc = {"today_kwh": 30.0}                                   # sol nok til at fylde batteriet
    c._decide_grid_charge(night)
    # 40 % er for lidt til natten og morgenen: lad kun op til ca. 49 % (1,5 kWh + 3,25 kWh / 0,95)
    assert c.plan.battery_action == "charge" and c.grid_charge["target_soc"] == 49, c.grid_charge
    h.states.get = {"sensor.soc": State("55")}.get; c.plan.battery_action = "charge"
    c._decide_grid_charge(night)
    assert c.plan.battery_action == "normal" and c.grid_charge["skipping"], c.grid_charge
    c.plan.battery_action = "charge"; C.fc = {"today_kwh": 2.0}  # gråvejr: lad op
    c._decide_grid_charge(night)
    assert c.plan.battery_action == "charge" and not c.grid_charge["skipping"], c.grid_charge
    c.plan.battery_action = "charge"; C.fc = {}                  # ingen prognose: uændret
    c._decide_grid_charge(night)
    assert c.plan.battery_action == "charge" and c.grid_charge is None
    # 18) Fælles energimåler for varme og varmt vand tælles kun én gang
    c, h = make({"heatpump_energy_sensor": "sensor.vp", "hotwater_energy_sensor": "sensor.vp",
                 "hotwater_entity": "switch.vand"}, {})
    c._heat_last, c._heat_cum = None, {"heatpump": 0.0, "hotwater": 0.0}
    c.enabled.update({"hotwater": True})
    assert c._shared_heat_meter
    c._split_heat_meter(100.0)                                  # første aflæsning: kun udgangspunkt
    r = c._split_heat_meter(101.0)                              # 1 kWh – ingen varmt vand nu: varme
    assert r == {"heatpump": 1.0, "hotwater": 0.0}, r
    c.plan.hotwater_on = True
    r = c._split_heat_meter(101.5)                              # 0,5 kWh mens vandet varmes
    assert r == {"heatpump": 1.0, "hotwater": 0.5}, r
    c, h = make({"heatpump_energy_sensor": "sensor.vp", "hotwater_energy_sensor": "sensor.vv"}, {})
    assert not c._shared_heat_meter
    # 19) Varme: pause over grænsen sætter grundtemperatur én gang; hævet grænse starter igen
    st = {"sensor.ude": State("9.7"), "climate.vp": State("heat", temperature=22.0)}
    c, h = make({"heating_climate_entity": "climate.vp", "outdoor_temp_sensor": "sensor.ude"}, st)
    c.enabled.update({"heating": True})
    c.heating_outdoor_ok = False                      # var på pause (det var over 10 °C tidligere)
    c.plan.heating_mode = "boost"
    c.settings["heating_base_temp"] = 20.5
    await c._apply_heating_step(c.plan, False); await c._apply_heating_step(c.plan, False)
    assert h.services.calls == [("climate", "set_temperature", {"entity_id": "climate.vp", "temperature": 20.5})]
    c.settings["heating_max_outdoor"] = 12.0          # grænsen hæves: 9,7 °C < 11 °C → aktiv igen
    c._last_cmd.clear()
    await c._apply_heating_step(c.plan, False)
    assert c.heating_outdoor_ok and h.services.calls[-1][2]["temperature"] == 21.5, h.services.calls
    # 20) "Lad i": faste perioder uden klar-tid – og de udløber af sig selv
    import datetime as _d
    c, h = make({}, {})
    today = _dt.datetime.now(ZoneInfo("Europe/Copenhagen")).date()
    c.ev_ready_mode = {"ev": ("tomorrow", today + _d.timedelta(days=1))}
    inp = c._ready_input("ev")
    assert inp["earliest"].date() == today + _d.timedelta(days=1)
    assert inp["deadline"].date() == today + _d.timedelta(days=2) and inp["deadline"].hour == 0
    c.ev_ready_mode = {"ev": ("today", today - _d.timedelta(days=1))}   # i går: udløbet
    assert c.ready_mode("ev") == ("next", None)
    c.ev_ready_mode = {"ev": ("horizon", None)}
    assert c._ready_input("ev") == {"horizon": True}
        # 10) Lille luft-til-luft varmepumpe: sluk i sparetimer, frostsikring og IR-scripts
    ac = {"climate.ac": State("heat", temperature=10)}
    c, h = make({"heating_type": "air_to_air", "heating_climate_entity": "climate.ac",
                 "heating_indoor_temp_sensor": "sensor.inde"}, {**ac, "sensor.inde": State("12")})
    c.settings.update({"heating_base_temp": 10.0, "heating_reduce_degrees": 2.0})
    c.flags["heating_off_in_reduce"] = True
    await c._set_heating("reduce", True)
    assert h.services.calls[-1] == ("climate", "set_hvac_mode", {"entity_id": "climate.ac", "hvac_mode": "off"})
    ac["climate.ac"] = State("off", temperature=10)
    c.hass.states = types.SimpleNamespace(get={**ac, "sensor.inde": State("12")}.get)
    await c._set_heating("normal", True)
    assert ("climate", "set_hvac_mode", {"entity_id": "climate.ac", "hvac_mode": "heat"}) in h.services.calls
    # Frostsikring: 7 °C inde (under 8) → spar ikke; først over 9 °C igen
    c.hass.states = types.SimpleNamespace(get={**ac, "sensor.inde": State("7")}.get)
    assert c._update_frost_guard() is True
    c.hass.states = types.SimpleNamespace(get={**ac, "sensor.inde": State("8.5")}.get)
    assert c._update_frost_guard() is True
    c.hass.states = types.SimpleNamespace(get={**ac, "sensor.inde": State("9.1")}.get)
    assert c._update_frost_guard() is False
    # Standardanlæg: ingen indeføler, ingen sluk – kun temperatur
    c, h = make({"heating_climate_entity": "climate.vp", "heating_indoor_temp_sensor": "sensor.inde"},
                {"climate.vp": State("heat", temperature=21), "sensor.inde": State("5")})
    c.flags["heating_off_in_reduce"] = True
    assert c.indoor_temp is None and not c.heating_off_in_reduce
    await c._set_heating("reduce", True)
    assert h.services.calls[-1][1] == "set_temperature"
    # Kun scripts (fx Broadlink): varmen tæller som opsat, scriptet køres ved skift
    c, h = make({"heating_type": "air_to_air", "heating_script_reduce": "script.ac_off",
                 "heating_script_normal": "script.ac_on"}, {})
    assert c.has_heating_control and c.has("heating_climate_entity")
    await c._set_heating("reduce", False); await c._set_heating("reduce", False)
    assert [x[2]["entity_id"] for x in h.services.calls] == ["script.ac_off"]
    # Elbil: efter man har ændret klar-tidspunkt, startes laderen ikke de næste 90 s – "Lad nu" starter altid
    c, h = make({}, {"switch.lader": State("off")})
    c.cars, c.car_ids, c.shared_chargers = [{"id": "ev", "name": "Bil", "charger": "switch.lader"}], ["ev"], {}
    c.enabled = {"master": True, "ev": True}
    c.ev_force, c._ev_hold = {"ev": False}, {}
    c.plan.evs = {"ev": planner.EvPlan("ev", on=True, connected=True)}
    c.hold_ev("ev")
    await c._apply_ev_step(c.plan, True)
    assert h.services.calls == [], h.services.calls          # venter – brugeren vælger tidspunkt
    c._ev_hold["ev"] = 0.0
    await c._apply_ev_step(c.plan, False)
    assert h.services.calls[-1] == ("switch", "turn_on", {"entity_id": "switch.lader"})
    # Lader slukket i Home Assistant, men bilen lader stadig (Easee fik ikke sluk-kommandoen): send igen efter 2 min
    c, h = make({}, {"switch.lader": State("off"), "sensor.ladeeffekt": State("7.2", unit_of_measurement="kW")})
    car = {"id": "ev", "name": "Bil", "charger": "switch.lader", "power": "sensor.ladeeffekt"}
    for _ in range(3):
        await c._ev_verify_off(car, False)
    assert h.services.calls == []
    await c._ev_verify_off(car, False)
    assert h.services.calls == [("switch", "turn_off", {"entity_id": "switch.lader"})]
    # Styring slået fra og til igen med det samme: normal drift sendes ikke (ingen kort opstart af laderen)
    c, h = make({}, {"switch.lader": State("off")})
    c.cars, c.car_ids, c.shared_chargers = [car], ["ev"], {}
    c.modules, c.enabled = ["ev"], {"master": True, "ev": True}
    c.recalculate = lambda: None
    async def no_apply(force=False): pass
    c.async_apply = no_apply
    await c.async_set_enabled("master", False)
    assert "ev" in c._restore_pending and h.services.calls == []
    await c.async_set_enabled("master", True)
    assert c._restore_pending == {} and h.services.calls == []
    # Bilens SoC står stille på 60 %, mens der lades med 11 kW: skønnet SoC stiger, så lademålet nås
    soc = {"v": "60"}
    c, h = make({}, {})
    c.hass.states = types.SimpleNamespace(get=lambda e: State(soc["v"]) if e == "sensor.soc"
                                          else State("11", unit_of_measurement="kW") if e == "sensor.p" else None)
    car = {"id": "ev", "name": "Kia", "charger": "switch.lader", "soc": "sensor.soc", "power": "sensor.p"}
    c.cars = [car]
    c.settings["ev_battery_capacity"] = 77.0
    c._track_soc()                               # første tal fra bilen
    assert c.car_soc(car) == 60.0
    for _ in range(240):                         # 2 timer à 30 s med 11 kW = 22 kWh
        c._track_soc()
    est = c.car_soc(car)
    assert 85 < est < 87, est                    # 60 % + 22 kWh × 0,9 / 77 kWh ≈ 85,7 %
    soc["v"] = "84"                              # bilen melder et nyt tal → det bruges igen
    c._track_soc()
    assert c.car_soc(car) == 84.0
    # Overståede planlagte ladekvarterer bliver stående på grafen
    from datetime import datetime as _d, timezone as _tz, timedelta as _td
    c, h = make({}, {})
    t0 = _d(2026, 10, 25, 1, 0, tzinfo=_tz.utc)
    c.plan.evs = {"ev": planner.EvPlan("ev", slots=[planner.Slot(t0, t0 + _td(minutes=15), 0, 1, 0)])}
    c._ev_past = {"ev": {t0 - _td(hours=1)}}
    shown = c.ev_slots_shown("ev")
    assert shown == [(t0 - _td(hours=1)).isoformat(), t0.isoformat()], shown
    # Styring af elbilen slået fra → normal drift = laderen slukket
    c, h = make({}, {"switch.lader": State("on")})
    c.cars, c.car_ids, c.shared_chargers = [{"id": "ev", "name": "Bil", "charger": "switch.lader"}], ["ev"], {}
    c.car = lambda cid: c.cars[0]
    await c.async_restore_normal("ev")
    assert h.services.calls[-1] == ("switch", "turn_off", {"entity_id": "switch.lader"})
    # Styring af bilen slået fra: "Lad nu" starter laderen, og den slukkes igen, når "Lad nu" slutter
    st = {"switch.lader": State("off")}
    c, h = make({}, st)
    c.cars, c.car_ids, c.shared_chargers = [{"id": "ev", "name": "Bil", "charger": "switch.lader"}], ["ev"], {}
    c.enabled = {"master": True, "ev": False}
    c.ev_force = {"ev": True}
    c.plan.evs = {"ev": planner.EvPlan("ev", connected=True)}
    await c._apply_ev_step(c.plan, True)
    assert h.services.calls[-1] == ("switch", "turn_on", {"entity_id": "switch.lader"})
    st["switch.lader"] = State("on")
    c.ev_force["ev"] = False
    await c._apply_ev_step(c.plan, True)
    assert h.services.calls[-1] == ("switch", "turn_off", {"entity_id": "switch.lader"})
    n = len(h.services.calls)
    await c._apply_ev_step(c.plan, False)              # derefter rører SmartHus ikke laderen
    assert len(h.services.calls) == n
    # Færdig opsætning (Deye): trinnene køres, når tilstanden skifter – med kortets grænser
    st = {"select.wm": State("Zero Export To CT", options=["Export First", "Zero Export To CT"]),
          "select.p1": State("Disabled", options=["Disabled", "Grid"]), "number.s1": State("15")}
    actions = {"charge": [{"type": "select", "entity_id": "select.p1", "value": "Grid"},
                          {"type": "number", "entity_id": "number.s1", "value": "max_soc"}],
               "normal": [{"type": "select", "entity_id": "select.p1", "value": "Disabled"},
                          {"type": "number", "entity_id": "number.s1", "value": "min_soc"}]}
    c, h = make({"battery_actions": actions}, st)
    c.settings["battery_max_soc"], c.settings["battery_min_soc"] = 90, 15
    assert c.has_battery_control
    await c._apply_battery("charge", False)
    assert h.services.calls == [("select", "select_option", {"entity_id": "select.p1", "option": "Grid"}),
                                ("number", "set_value", {"entity_id": "number.s1", "value": 90.0})], h.services.calls
    await c._apply_battery("charge", False)          # samme tilstand: intet nyt
    assert len(h.services.calls) == 2
    print("controller OK")

def test_controller_control():
    asyncio.run(main())


def test_manual_soc():
    """Bil uden SoC-sensor: indtastet batteriniveau, skøn under opladning, ekstra tid og 'bilen er fuld'."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    car = {"id": "ev", "name": "Bil", "charger": "switch.lader", "power": "sensor.ladeeffekt"}
    states = {"switch.lader": State("on"), "sensor.ladeeffekt": State("11", unit_of_measurement="kW")}
    c, h = make({}, states)
    c.cars, c.manual_soc, c._ev_idle, c._ev_full_key = [car], {}, {}, {}
    c.connected, c.connected_source = {"ev": True}, {"ev": "sensor"}
    ready = _dt.now(_tz.utc) + _td(hours=8)
    c.ready_at = lambda cid: ready
    c.recalculate = lambda: None
    c.settings.update({"ev_target_soc": 80.0, "ev_battery_capacity": 60.0, "ev_charge_power": 11.0, "ev_extra_hours": 2.0})
    assert c.car_soc(car) is None and c._extra_hours(car) == 0.0     # intet tastet: timer-tilstand som før
    c.set_manual_soc("ev", 40)
    assert c.car_soc(car) == 40.0
    # 1 time ved 11 kW → 11 kWh · 0,9 / 60 kWh = +16,5 %
    for _ in range(120):
        c._track_soc()
    assert abs(c.car_soc(car) - 56.5) < 0.2, c.car_soc(car)
    assert c._extra_hours(car) == 0.0                                 # "bilen stopper selv" er slået fra
    c.flags["ev_car_target"] = True
    assert c._extra_hours(car) == 2.0
    # Bilen holder op med at trække strøm i et kvarter → fuld, ingen ekstra tid tilbage
    states["sensor.ladeeffekt"] = State("0.0", unit_of_measurement="kW")
    c._track_soc()
    c._ev_idle["ev"] -= ctl_mod.EV_FULL_IDLE_SEC + 1
    c._track_soc()
    assert c.manual_soc["ev"]["done"] and c.car_soc(car) == 80.0 and c._extra_hours(car) == 0.0
    # Bilen kobles fra → det indtastede tal glemmes
    c.connected["ev"] = False
    c._track_soc()
    assert c.car_soc(car) is None
    # Planen: ekstra tid lægges oven i, men "klar" og prisen regnes kun til målet
    n = _dt(2026, 10, 30, 12, 0, tzinfo=_tz.utc)
    slots = [planner.Slot(n + _td(hours=i), n + _td(hours=i + 1), 1.0 + (i % 3) * 0.1, 1.0, 0.4) for i in range(24)]
    st = {**c.settings, "ev_ready_hour": 23.0}
    base = planner.build_plan(slots, n, c.tz, st, cars=[planner.CarInput("ev", soc=62.0)]).evs["ev"]
    more = planner.build_plan(slots, n, c.tz, st, cars=[planner.CarInput("ev", soc=62.0, extra_h=2.0)]).evs["ev"]
    assert base.hours_needed == more.hours_needed and base.energy == more.energy
    assert sum(s.hours for s in more.slots) > sum(s.hours for s in base.slots) + 1.9
    assert more.done_at is not None and more.done_at <= max(s.end for s in more.slots)
    full = planner.build_plan(slots, n, c.tz, st, cars=[planner.CarInput("ev", soc=80.0, extra_h=1.0)]).evs["ev"]
    assert not full.target_reached and full.slots          # målet skønsmæssigt nået: sikkerhedstiden køres


def test_select_charger():
    """Lader styret med rullemenu (fx go-e 'Force state'): tænd/sluk via mulighederne."""
    car = {"id": "ev", "name": "Bil", "charger": "select.goe_frc", "charger_on": "2", "charger_off": "1"}
    states = {"select.goe_frc": State("1", options=["0", "1", "2"])}
    c, h = make({}, states)
    c.cars = [car]
    assert not c._charger_on(car)
    asyncio.run(c._set_charger(car, True, True))
    assert h.services.calls[-1] == ("select", "select_option", {"entity_id": "select.goe_frc", "option": "2"})
    states["select.goe_frc"] = State("2", options=["0", "1", "2"])
    assert c._charger_on(car)
    asyncio.run(c._set_charger(car, False, True))
    assert h.services.calls[-1] == ("select", "select_option", {"entity_id": "select.goe_frc", "option": "1"})
    sw = {"id": "ev2", "charger": "switch.lader"}
    states["switch.lader"] = State("off")
    asyncio.run(c._set_charger(sw, True, True))
    assert h.services.calls[-1] == ("switch", "turn_on", {"entity_id": "switch.lader"})


def test_growatt_cloud_control():
    """Growatt via skyen: blokkens tidsrum skrives én gang, fejl prøves igen, sælg sol → normal."""
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 2, 0, tzinfo=tz).astimezone(timezone.utc)
    ch = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 1.0, 1.0, 0.5) for i in range(8)]
    c, h = make({"battery_growatt_device": "gw1", "battery_growatt_type": "sph"}, {})
    c.settings.update(battery_max_soc=90, battery_min_soc=20)
    c.plan = planner.Plan(current=ch[2], battery_charge=ch)
    assert c.has_battery_control
    asyncio.run(c._apply_battery("charge", False))
    assert [x[1] for x in h.services.calls] == ["write_ac_discharge_times", "write_ac_charge_times"]
    data = h.services.calls[1][2]
    assert (data["period_1_start"], data["period_1_end"], data["charge_stop_soc"], data["charge_power"]) == \
        ("02:00", "04:00", 90, 100)
    c.plan = planner.Plan(current=ch[3], battery_charge=ch)       # næste kvarter i samme blok: intet nyt
    asyncio.run(c._apply_battery("charge", False))
    assert len(h.services.calls) == 2
    c._last_cmd.clear()
    asyncio.run(c._apply_battery("sell_solar", False))             # findes ikke via skyen → normal
    assert [x[1] for x in h.services.calls[2:]] == ["write_ac_charge_times", "write_ac_discharge_times"]
    assert h.services.calls[2][2]["mains_enabled"] is False

    # Et kald fejler: tilstanden huskes ikke, og der prøves igen (efter ventetiden)
    c, h = make({"battery_growatt_device": "gw1"}, {})
    c.plan = planner.Plan(current=ch[0], battery_discharge=ch)
    fails = {"n": 1}
    async def flaky(domain, service, data, blocking=False):
        h.services.calls.append((domain, service, data))
        if fails["n"]:
            fails["n"] -= 1
            raise RuntimeError("Growatt API timeout")
    h.services.async_call = flaky
    asyncio.run(c._apply_battery("discharge", False))
    assert len(h.services.calls) == 1 and "battery" not in c._script_state
    asyncio.run(c._apply_battery("discharge", False))              # inden for 90 s: venter
    assert len(h.services.calls) == 1
    c._last_cmd.clear()
    asyncio.run(c._apply_battery("discharge", False))
    assert [x[1] for x in h.services.calls[1:]] == ["write_ac_charge_times", "write_ac_discharge_times"]
    assert c._script_state["battery"] == "discharge@02:00-04:00"


def test_split_power_sensors_give_energy_flow():
    """Køb/salg og lad/aflad som to sensorer hver (Growatt via skyen) giver net, batteri og hus."""
    states = {"sensor.imp": State("543.5", unit_of_measurement="W"), "sensor.exp": State("0", unit_of_measurement="W"),
              "sensor.chg": State("0", unit_of_measurement="W"), "sensor.dis": State("1200", unit_of_measurement="W"),
              "sensor.pv": State("0.3", unit_of_measurement="kW")}
    cfg = {"grid_import_power_sensor": "sensor.imp", "grid_export_power_sensor": "sensor.exp",
           "battery_charge_power_sensor": "sensor.chg", "battery_discharge_power_sensor": "sensor.dis",
           "solar_power_sensor": "sensor.pv"}
    c, h = make(cfg, states)
    c.cars = []
    assert c.has("grid_power_sensor")
    assert abs(c.export_kw - (-0.5435)) < 1e-9 and abs(c.battery_kw - 1.2) < 1e-9
    f = c.flow
    assert f["grid_kw"] == 0.54 and f["battery_kw"] == 1.2 and f["house_kw"] == 2.04 and f["has_battery"]
    # Salg og opladning
    states["sensor.imp"], states["sensor.exp"] = State("0", unit_of_measurement="W"), State("800", unit_of_measurement="W")
    states["sensor.chg"], states["sensor.dis"] = State("1500", unit_of_measurement="W"), State("0", unit_of_measurement="W")
    assert abs(c.export_kw - 0.8) < 1e-9 and abs(c.battery_kw - (-1.5)) < 1e-9
    # Mangler den ene: intet tal (hellere tomt end forkert)
    del states["sensor.exp"]
    assert c.export_kw is None
    c2, _ = make({"grid_import_power_sensor": "sensor.imp"}, states)
    assert not c2.has("grid_power_sensor")
    # Én samlet sensor har forrang
    states["sensor.grid"] = State("-300", unit_of_measurement="W")
    c3, _ = make({**cfg, "grid_power_sensor": "sensor.grid"}, states)
    assert abs(c3.export_kw - (-0.3)) < 1e-9


def test_growatt_hold_blocks_discharge_without_grid_charge():
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 11, 0, tzinfo=tz).astimezone(timezone.utc)
    hold = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 2.7, 2.7, 1.8) for i in range(12)]
    c, h = make({"battery_growatt_device": "gw1", "battery_growatt_type": "sph"}, {})
    assert c.has_hold
    c.plan = planner.Plan(current=hold[1], battery_hold=hold)
    asyncio.run(c._apply_battery("hold", False))
    # Battery First med ladeeffekt 1 % og uden netopladning: batteriet står stille (afprøvet på SPH)
    assert [x[1] for x in h.services.calls] == ["write_ac_discharge_times", "write_ac_charge_times"]
    d = h.services.calls[1][2]
    assert d["mains_enabled"] is False and d["charge_power"] == 1
    assert (d["period_1_start"], d["period_1_end"], d["period_1_enabled"]) == ("11:00", "14:00", True)
    # MIN kan ikke stå stille: normal drift
    c2, h2 = make({"battery_growatt_device": "gw1", "battery_growatt_type": "min"}, {})
    assert not c2.has_hold
    c2.plan = planner.Plan(current=hold[1], battery_hold=hold)
    asyncio.run(c2._apply_battery("hold", False))
    assert h2.services.calls[-1][2]["enabled"] is False


def test_growatt_hold_lets_sun_charge():
    """"Gem batteriet" på SPH (1 %) stopper også solen – ved overskud i 2 min køres normal drift, så solen
    lader batteriet; dækker batteriet huset i 3 min, gemmes det igen (afprøvet på et rigtigt anlæg: 0,33 kW eksport, 18 %)."""
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 4, 9, 0, tzinfo=tz).astimezone(timezone.utc)
    hold = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 2.7, 2.7, 1.8) for i in range(32)]
    st = {"sensor.grid": State("330", unit_of_measurement="W"), "sensor.bat": State("0", unit_of_measurement="W"),
          "sensor.soc": State("18")}
    c, h = make({"battery_growatt_device": "gw1", "battery_growatt_type": "sph", "grid_power_sensor": "sensor.grid",
                 "battery_power_sensor": "sensor.bat", "battery_soc_sensor": "sensor.soc"}, st)
    c.plan = planner.Plan(current=hold[1], battery_hold=hold)
    clock = [1000.0]
    real = ctl_mod.time.monotonic
    ctl_mod.time.monotonic = lambda: clock[0]
    try:
        def tick(sec):
            clock[0] += sec
            c._last_cmd.clear()
            asyncio.run(c._apply_battery("hold", False))
            return c._script_state.get("battery", "")
        assert tick(0).startswith("hold@")                 # overskud lige startet: gem stadig
        assert tick(60).startswith("hold@")
        assert tick(61) == "normal" and c.hold_sun          # 2 min overskud: solen lader batteriet
        st["sensor.grid"] = State("0", unit_of_measurement="W")
        st["sensor.bat"] = State("-300", unit_of_measurement="W")   # lader fra solen: stadig overskud
        assert tick(300) == "normal"
        st["sensor.bat"] = State("250", unit_of_measurement="W")    # solen for svag: batteriet dækker huset
        assert tick(60) == "normal"
        assert tick(130) == "normal"
        assert tick(60).startswith("hold@") and not c.hold_sun          # 3 min: gem igen
        asyncio.run(c._apply_battery("charge", False))
        assert not c.hold_sun and c._hold_sun_since is None
    finally:
        ctl_mod.time.monotonic = real


def test_history_shows_only_done_charging():
    """Grafen: overståede ladekvarterer kun, hvis batteriet faktisk blev ladet – kommende vises som planlagt."""
    from datetime import timedelta
    q = timedelta(minutes=15)
    now = ctl_mod.dt_util.utcnow()
    t0 = now.replace(second=0, microsecond=0) - 4 * q
    ch = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 1.0, 1.0, 0.5) for i in range(8)]
    c, h = make({}, {})
    c._plan_past = {}
    c.plan = planner.Plan(current=ch[1], battery_charge=ch, battery_action="hold")   # målet nået: ladede ikke
    c._remember_past(ch[1].start)
    c.plan = planner.Plan(current=ch[2], battery_charge=ch, battery_action="charge")
    c._remember_past(ch[2].start)
    shown = {s.start for s in c.done_shown("battery_charge", ch)}
    assert ch[0].start not in shown and ch[1].start not in shown and ch[2].start in shown
    assert all(s.start in shown for s in ch if s.start > now)


def test_hold_capability_and_fallbacks():
    c, h = make({"battery_mode_entity": "select.mode", "battery_option_hold": "Hold"}, {})
    assert c.has_hold and c._battery_option("hold") == "Hold"
    c, h = make({"battery_mode_entity": "select.mode"}, {})
    assert not c.has_hold and c._battery_option("hold") == "Maximum Self Consumption"
    c, h = make({"battery_actions": {"normal": [], "charge": []}}, {})
    assert not c.has_hold
    c, h = make({"battery_script_charge": "script.c", "battery_script_discharge": "script.d",
                 "battery_script_normal": "script.n"}, {})
    assert not c.has_hold
    asyncio.run(c._apply_battery("hold", False))                      # uden gem-script: normal-scriptet
    assert h.services.calls[-1][2]["entity_id"] == "script.n"
    c, h = make({"battery_script_normal": "script.n", "battery_script_hold": "script.h"}, {})
    assert c.has_hold
    asyncio.run(c._apply_battery("hold", False))
    assert h.services.calls[-1][2]["entity_id"] == "script.h"
    c, h = make({"battery_huawei_device": "d"}, {})
    assert not c.has_hold
    asyncio.run(c._apply_battery("hold", False))
    assert h.services.calls[-1][1] == "stop_forcible_charge"


def test_charge_target_reached_holds_rest_of_block():
    """Når lademålet er nået, står batteriet stille resten af ladeblokken (ikke normal drift i de billigste timer)."""
    import types
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 2, 0, tzinfo=tz).astimezone(timezone.utc)
    ch = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 2.4, 2.4, 1.6) for i in range(12)]
    c, h = make({"battery_growatt_device": "gw1", "battery_growatt_type": "sph", "battery_soc_sensor": "sensor.soc"},
                {"sensor.soc": State("85")})
    c.settings.update(battery_capacity=5.6, battery_max_soc=95, battery_min_soc=15)
    c.plan = planner.Plan(current=ch[5], battery_charge=ch, battery_action="charge")
    c.plan.battery_hold = []
    c.profile = sys.modules["smarthus.profile"].Profile(weekday=[0.3] * 24, weekend=[0.3] * 24, days=5)
    c.coordinator = types.SimpleNamespace(data=ch)
    c._forecast_wh, c._forecast_key_is_end = {}, False
    fc = {"today_kwh": 9.0, "remaining_kwh": 9.0, "tomorrow_kwh": 9.0}
    c.__class__ = type("WithSun", (type(c),), {"solar_forecast": property(lambda self: fc)})
    c._decide_grid_charge(ch[5].start)
    assert c.grid_charge["skipping"] and c.plan.battery_action == "hold"
    c._decide_hold(ch[5].start, ch[-1].end)
    hold = c.plan.battery_hold
    assert hold[0].start == ch[5].start and hold[-1].end == ch[-1].end          # resten af blokken som ét tidsrum
    asyncio.run(c._apply_battery("hold", False))
    d = h.services.calls[-1][2]
    assert d["charge_power"] == 1 and (d["period_1_start"], d["period_1_end"]) == ("03:15", "05:00")



def test_full_charge_hours_setting():
    """"Lad batteriet helt op" virker først, når maks. ladeeffekt er tastet ind."""
    c, h = make({}, {})
    c.settings.update({"battery_capacity": 10.0, "battery_min_soc": 15.0, "battery_max_soc": 95.0, "battery_max_charge_kw": 0.0})
    assert c.full_charge_hours is None
    c.flags["battery_full_charge"] = True
    assert c.full_charge_hours is None                  # ingen effekt tastet ind endnu
    c.settings["battery_max_charge_kw"] = 5.0
    assert c.full_charge_hours == 1.75
    c.flags["battery_full_charge"] = False
    assert c.full_charge_hours is None


def test_past_forecast_slots_kept():
    """"Batteri bruges/gemmes" og sol forsvinder ikke fra grafen, når tiden er gået."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    c, h = make({}, {})
    c._plan_past = {}
    n = _dt(2026, 11, 2, 15, 0, tzinfo=_tz.utc)
    sl = lambda hh: planner.Slot(n + _td(hours=hh), n + _td(hours=hh, minutes=15), 2.0, 1.0, 0.4)
    c.plan.battery_use = [sl(-1), sl(0), sl(2)]
    c._remember_past(n)
    c.plan.battery_use = [sl(2)]                     # næste fremskrivning starter fra nu
    shown = sorted(x.start for x in c.slots_shown("battery_use", c.plan.battery_use))
    assert shown == [n - _td(hours=1), n, n + _td(hours=2)]
    c._remember_past(n + _td(hours=40))              # ældre end 36 timer glemmes
    assert c.slots_shown("battery_use", []) == []


def test_solar_only_when_control_off():
    """Styring af bilen slået fra + "lad med solcelleoverskud": lad kun med overskuddet, sluk når solen er væk."""
    car = {"id": "ev", "name": "Bil", "charger": "switch.lader", "current_entity": "number.strom"}
    states = {"switch.lader": State("off"), "number.strom": State("6"),
              "sensor.grid": State("5000", unit_of_measurement="W"), "sensor.sol": State("6000", unit_of_measurement="W")}
    c, h = make({"grid_power_sensor": "sensor.grid", "solar_power_sensor": "sensor.sol"}, states)
    c.cars, c.flags["ev_solar"] = [car], True
    c.ev_force = {}
    ep = planner.EvPlan("ev")
    asyncio.run(c._apply_ev_manual(car, ep, False)); assert not any(x[0] == "switch" for x in h.services.calls)
    asyncio.run(c._apply_ev_manual(car, ep, False))          # 2 × 30 s med sol: start
    assert ("switch", "turn_on", {"entity_id": "switch.lader"}) in h.services.calls
    states["switch.lader"] = State("on")
    states["sensor.sol"] = State("50", unit_of_measurement="W"); states["sensor.grid"] = State("-1500", unit_of_measurement="W")
    for _ in range(10):
        c._recently_sent = lambda e: False
        asyncio.run(c._apply_ev_manual(car, ep, False))
    assert h.services.calls[-1] == ("switch", "turn_off", {"entity_id": "switch.lader"})
    # Slås solcelleladning fra, slukkes laderen med det samme
    c2, h2 = make({"grid_power_sensor": "sensor.grid", "solar_power_sensor": "sensor.sol"},
                  {**states, "sensor.sol": State("6000", unit_of_measurement="W"), "sensor.grid": State("5000", unit_of_measurement="W"),
                   "switch.lader": State("off")})
    c2.cars, c2.flags["ev_solar"] = [car], True
    c2.ev_force = {}
    asyncio.run(c2._apply_ev_manual(car, ep, False)); asyncio.run(c2._apply_ev_manual(car, ep, False))
    c2.hass.states = types.SimpleNamespace(get={**states, "switch.lader": State("on")}.get)
    c2.flags["ev_solar"] = False
    asyncio.run(c2._apply_ev_manual(car, ep, True))
    assert h2.services.calls[-1] == ("switch", "turn_off", {"entity_id": "switch.lader"})


def test_grid_charge_hours_follow_need_and_solar_forecast():
    """Netopladning time for time: kun det, der mangler til i morgen, ved den indtastede ladeeffekt.
    Tal fra et rigtigt anlæg: 5,6 kWh, 10–100 %, 3 kW, 8,7 kWh sol i morgen – lad ca. 1 time til ca. 60–67 %, ikke 3 timer."""
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Copenhagen")
    prof = sys.modules["smarthus.profile"]
    wd = [0.33] * 9 + [5.0 / 12] * 12 + [0.4] * 3
    tom = datetime(2026, 11, 4, tzinfo=tz)
    shape = {8: .3, 9: .7, 10: 1.1, 11: 1.4, 12: 1.4, 13: 1.2, 14: .9, 15: .5, 16: .2}
    k = 8.72 / sum(shape.values())
    solar = {tom.replace(hour=h): v * k * 0.7 for h, v in shape.items()}
    c, h = make({"battery_soc_sensor": "sensor.soc"}, {})
    c.tz, c.profile = tz, prof.Profile(weekday=wd, weekend=wd, days=14)
    c._solar_hours = lambda: solar
    c.settings.update(battery_capacity=5.6, battery_min_soc=10, battery_max_soc=100,
                      battery_max_charge_kw=3, battery_charge_hours=3)
    evening = tom - timedelta(hours=2)                         # 22:00 aftenen før, 20 %
    assert c._grid_charge_hours(evening, 20) == 1.25
    night = tom.replace(hour=1)
    assert c._grid_charge_hours(night, 10) == 1.0
    n = c._night_need(night)
    target = n["need_at_sun"] + prof.expected_load(c.profile, night, n["sun"]) / prof.EFFICIENCY
    assert 55 <= target / 5.6 * 100 <= 67
    # Ingen sol i morgen: batteriet skal fyldes (begrænset af 'Timer at lade' i planen)
    c._solar_hours = lambda: {}
    assert c._grid_charge_hours(night, 10) >= 1.75
    # Ingen ladeeffekt sat: uændret adfærd
    c.settings["battery_max_charge_kw"] = 0
    assert c._grid_charge_hours(night, 10) is None


def test_growatt_normal_resets_charge_power():
    """Efter en gem-periode (1 %) sættes ladeeffekten tilbage til 100 %, så solen kan lade batteriet."""
    gw = sys.modules["smarthus.growatt"]
    cmds = gw.commands("sph", "gw1", "normal", None, 100, 10, 100)
    charge = [d for s, d in cmds if s == "write_ac_charge_times"][0]
    assert charge["charge_power"] == 100 and charge["mains_enabled"] is False


def test_learn_solar_adjusts_forecast_and_plan():
    """Hele vejen: statistik og tidligere prognose → faktorer → justeret prognose til kort og batteriplan."""
    suncal = sys.modules["smarthus.suncal"]
    tz = ZoneInfo("Europe/Copenhagen")
    now = _dt.datetime.now(tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    forecast_rows, meter_rows = [], []
    for d in range(1, 13):
        for hour, fc, actual in ((7, 0.2, 0.0), (10, 1.0, 2.0), (14, 1.0, 0.2)):
            start = (midnight - _dt.timedelta(days=d)).astimezone(timezone.utc).replace(hour=hour)
            forecast_rows.append({"start": start.timestamp(), "mean": fc})
            meter_rows.append({"start": start.timestamp(), "change": actual})
    stats = {"sensor.fc_power_production_now": forecast_rows, "sensor.solar": meter_rows}
    asked = {}

    def during(_hass, _start, _end, ids, period, units, types_):
        asked.update(ids=ids, period=period, units=units, types=types_)
        return stats

    class Rec:
        async def async_add_executor_job(self, fn, *args):
            return fn(*args)

    class Saved:
        data = None
        async def async_save(self, data):
            Saved.data = data

    entries = [types.SimpleNamespace(entity_id="sensor.fc_power_production_now"), types.SimpleNamespace(entity_id="sensor.fc_energy")]
    stub("homeassistant.components"); stub("homeassistant.components.recorder", get_instance=lambda h: Rec())
    stub("homeassistant.components.recorder.statistics", statistics_during_period=during)
    registry = stub("homeassistant.helpers.entity_registry", async_get=lambda h: None,
                    async_entries_for_config_entry=lambda reg, entry_id: entries)
    sys.modules["homeassistant.helpers"].entity_registry = registry

    c, h = make({"solar_energy_sensor": "sensor.solar", "solar_power_sensor": "sensor.solar_power"}, {})
    h.config_entries = types.SimpleNamespace(async_entries=lambda domain: [types.SimpleNamespace(entry_id="1")])
    c._solar_cal_store = Saved()
    c.recalculate = lambda: None
    c._forecast_domain = "forecast_solar"
    c._forecast_ids = lambda: {}
    c._fc_ids = {}
    asyncio.run(c.async_learn_solar())
    assert asked["ids"] == {"sensor.solar", "sensor.fc_power_production_now"} and asked["period"] == "hour"
    assert c.solar_cal.active and c.solar_cal.days == 12 and Saved.data is c.solar_cal.data

    # Kort og plan bruger den justerede prognose: morgen og eftermiddag ned, formiddag op
    tomorrow = midnight + _dt.timedelta(days=1)
    wh = {}
    for hour in (7, 10, 14):
        wh[(tomorrow + _dt.timedelta(hours=12)).astimezone(timezone.utc).replace(hour=hour).isoformat()] = 1000.0
    c._forecast_wh = wh
    plan = {k.astimezone(timezone.utc).hour: v for k, v in c._solar_hours().items()}
    assert plan[7] < 0.3 * 0.7
    assert plan[10] > 1.5 * 0.7
    assert plan[14] < 0.5 * 0.7
    shown = c.solar_forecast
    assert shown["adjusted"] and shown["learned_days"] == 12
    assert abs(shown["tomorrow_kwh"] - sum(v for v in plan.values()) / 0.7) < 0.02

    # Uden læring er prognosen præcis, som den plejer
    c2, _ = make({}, {})
    c2._forecast_wh = wh
    c2._forecast_ids = lambda: {}
    assert {k: round(v, 6) for k, v in c2._solar_hours().items()} == {
        k: round(1.0 * 0.7, 6) for k in c2._solar_hours()}
    assert not c2.solar_forecast["adjusted"]

    # Forsigtigheden kan justeres: 100 % bruger hele prognosen, 40 % kun en del – kortet viser den fulde
    c.settings["battery_solar_forecast_pct"] = 100
    full = {k.astimezone(timezone.utc).hour: v for k, v in c._solar_hours().items()}
    assert abs(full[10] - plan[10] / 0.7) < 1e-6
    c.settings["battery_solar_forecast_pct"] = 40
    low = {k.astimezone(timezone.utc).hour: v for k, v in c._solar_hours().items()}
    assert abs(low[10] - full[10] * 0.4) < 1e-6
    assert c.solar_forecast["tomorrow_kwh"] == shown["tomorrow_kwh"]
    assert c.solar_factor_today == 0.4
    c.settings.pop("battery_solar_forecast_pct")
    assert c.solar_caution == 0.7    # installationer fra før indstillingen fandtes


def test_planned_charge_can_be_read_in_advance():
    """Hvor meget der lades i nat, står klar før opladningen – og skønnet følger samme mål som styringen."""
    prof = sys.modules["smarthus.profile"]
    tz = ZoneInfo("Europe/Copenhagen")
    now = _dt.datetime(2026, 11, 3, 19, 0, tzinfo=tz)
    week = [0.38, 0.27, 0.52, 0.33, 0.3, 0.3, 0.42, 0.42, 0.27, 0.27, 0.45, 0.27, 0.42, 0.27, 0.3, 0.27, 0.32, 0.84, 1, 0.67, 0.62, 0.63, 0.4, 0.27]
    om = {7: .01025, 8: .27825, 9: .6235, 10: .97225, 11: 1.47425, 12: 1.6725, 13: 1.539, 14: 1.22625, 15: .7775, 16: .14325}
    c, _ = make({"battery_mode_entity": "select.mode", "battery_soc_sensor": "sensor.soc"}, {"sensor.soc": State("84")})
    c.settings.update(battery_capacity=5.6, battery_min_soc=10, battery_max_soc=100, battery_max_charge_kw=3)
    c.profile = prof.Profile(weekday=week, weekend=week, days=9, weekday_days=7, weekend_days=2)
    c._forecast_wh = {_dt.datetime(2026, 11, 4, h, tzinfo=tz).isoformat(): v * 1000 for h, v in om.items()}
    slots, t = [], now.replace(minute=0)
    while t < _dt.datetime(2026, 11, 5, tzinfo=tz):
        slots.append(planner.Slot(t, t + _dt.timedelta(minutes=15), 0, 2.17 if 3 <= t.hour < 4 and t.day == 4 else 3.0, 1.4))
        t += _dt.timedelta(minutes=15)
    c.coordinator = types.SimpleNamespace(data=slots)
    c.plan.battery_charge = [s for s in slots if s.price == 2.17]
    c.plan.current = slots[0]
    c.flags["battery_sell_solar"] = False
    pc = c._plan_grid_charge(now)
    assert pc["start"].startswith("2026-11-04T03:00") and pc["end"].startswith("2026-11-04T04:00")
    assert 2.2 <= pc["kwh"] <= 2.85, pc                      # højst det, 1 time ved 3 kW kan nå
    assert 55 <= pc["target_soc"] <= 75, pc
    assert abs(pc["cost"] - pc["kwh"] * 2.17) < 0.15
    target = c._charge_target(c.plan.battery_charge[0].start, 2.17)
    assert 3.3 < target < 3.9, target                        # ikke fuldt op
    # Fremskrivningen lader også kun op til målet
    f = prof.forecast_battery(slots, c.profile, now, battery_kwh=4.7, floor_kwh=0.56, full_kwh=5.6, charge=c.plan.battery_charge,
                              hold=[], sell=[], stock_price=2.2, can_hold=False, solar=c._solar_hours(),
                              charge_target=lambda s: target)
    assert 2.0 < f["days"]["2026-11-04"]["charge_kwh"] < 3.2, f["days"]["2026-11-04"]
    # Batteriet har rigeligt: ingen opladning
    c.hass.states = types.SimpleNamespace(get={"sensor.soc": State("100")}.get)
    c.profile = prof.Profile(weekday=[0.02] * 24, weekend=[0.02] * 24, days=9, weekday_days=7, weekend_days=2)
    assert c._plan_grid_charge(now) is None
    # Intet ladetidsrum
    c.plan.battery_charge = []
    assert c._plan_grid_charge(now) is None


def test_sell_waits_after_reserve_stop():
    """Salg stoppet ved reserven starter først igen ved mindst 0,5 kWh til salg."""
    ct = sys.modules["smarthus.controller"]
    src = open(ct.__file__, encoding="utf8").read()
    assert "SELL_RESTART_KWH" in src and ct.SELL_RESTART_KWH == 0.5


def test_battery_not_recharged_right_after_reaching_max():
    """Billig time og 97 % af 98 %: lad, medmindre batteriet lige har været fuldt."""
    planner = sys.modules["smarthus.planner"]
    from datetime import datetime as D, timedelta as T, timezone as Z
    now = D(2026, 11, 9, 2, 5, tzinfo=Z.utc)
    start = now.replace(minute=0)
    slots = [planner.Slot(start + T(hours=i), start + T(hours=i + 1), 0, 1.0 if i < 3 else 4.0, 0.5) for i in range(20)]
    c, h = make({}, {})
    s = dict(c.settings, battery_max_soc=98.0, battery_min_soc=7.0)
    assert planner.build_plan(slots, now, Z.utc, s, soc=97.0, cars=[]).battery_action == "charge"
    assert planner.build_plan(slots, now, Z.utc, s, soc=97.0, cars=[], battery_full=True).battery_action != "charge"


def test_default_profile_until_learned():
    """Standardforbrug bruges til planen; det lærte (tomme) mønster er det, der vises."""
    prof = sys.modules["smarthus.profile"]
    p = prof.default_profile()
    assert p.ready and p.weekday[16] == 0.5 and p.weekday[17] == 1.5 and p.weekend[19] == 1.5 and p.weekday[20] == 0.5
    assert sum(p.weekday) == 15.0
    # Billigt om eftermiddagen, dyrt kl. 17–20: et lille batteri gemmes til aftenen
    from datetime import datetime as D, timedelta as T
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen"); now = D(2026, 11, 9, 14, 0, tzinfo=tz)
    slots = [types.SimpleNamespace(start=now + T(hours=i), end=now + T(hours=i + 1), price=4.0 if 17 <= 14 + i < 20 else 2.0) for i in range(8)]
    hold, _ = prof.plan_hold(slots, p, now, now + T(hours=8), usable_kwh=4.8)
    assert hold and all(s.price == 2.0 for s in hold)


def test_inverter_reserve_follows_min_soc():
    """Inverterens nedre grænse følger "Laveste niveau"; rører den ikke, når de er ens eller ukendt."""
    states = {"number.reserve": State("5.0", min=0, max=100)}
    c, h = make({"battery_reserve_entity": "number.reserve"}, states)
    c.settings["battery_min_soc"] = 7.0
    asyncio.run(c._async_sync_reserve())
    assert [(x[1], x[2]["value"]) for x in h.services.calls if x[0] == "number"] == [("set_value", 7.0)]
    h.services.calls.clear()
    states["number.reserve"] = State("7.0", min=0, max=100)
    asyncio.run(c._async_sync_reserve())
    states["number.reserve"] = State("unavailable")
    asyncio.run(c._async_sync_reserve())
    assert not [x for x in h.services.calls if x[0] == "number"]


def test_no_soc_car_starts_at_zero_when_plugged():
    """Bil uden SoC med kabelsensor: 0 % når den sættes til, skønnet består over klar-tidspunktet, glemmes når den kører."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    car = {"id": "ev", "name": "Bil", "charger": "switch.lader", "power": "sensor.ladeeffekt", "plug": "sensor.kabel"}
    states = {"switch.lader": State("off"), "sensor.ladeeffekt": State("0", unit_of_measurement="kW")}
    c, h = make({}, states)
    c.cars = [car]
    c.connected, c.connected_source = {"ev": False}, {"ev": "sensor"}
    c.ready_at = lambda cid: _dt.now(_tz.utc) + _td(hours=8)
    c.recalculate = lambda: None
    c.settings.update({"ev_target_soc": 80.0, "ev_battery_capacity": 60.0, "ev_charge_power": 11.0})
    c._track_soc()
    assert c.car_soc(car) is None                  # ikke tilsluttet: intet tal
    c.connected["ev"] = True
    c._track_soc()
    assert c.car_soc(car) == 0.0                   # sat til: 0 %
    c.set_manual_soc("ev", 50)
    c._track_soc()
    assert c.car_soc(car) == 50.0                  # det indtastede overskrives ikke
    c.manual_soc["ev"]["expires"] = (_dt.now(_tz.utc) - _td(hours=1)).isoformat()
    c._track_soc()
    assert c.car_soc(car) == 50.0                  # klar-tidspunktet gået, bilen står der stadig
    assert _dt.fromisoformat(c.manual_soc["ev"]["expires"]) > _dt.now(_tz.utc)
    c.connected_source["ev"] = "assumed"           # sensoren kortvarigt ukendt: ingen nulstilling
    c._track_soc()
    assert c.car_soc(car) == 50.0
    c.connected_source["ev"] = "sensor"
    c.connected["ev"] = False
    c._track_soc()
    assert c.car_soc(car) is None                  # kørt


def test_manual_soc_when_sensor_unavailable():
    """Bilens SoC-sensor svarer ikke: det indtastede batteriniveau bruges – og glemmes, når sensoren virker igen."""
    car = {"id": "ev", "name": "Bil", "charger": "switch.lader", "soc": "sensor.bil_soc"}
    states = {"switch.lader": State("off"), "sensor.bil_soc": State("unavailable")}
    c, h = make({}, states)
    c.cars, c.manual_soc, c._ev_idle, c._ev_full_key = [car], {}, {}, {}
    c.connected, c.connected_source = {"ev": True}, {"ev": "assumed"}
    c.ready_at = lambda cid: __import__("datetime").datetime.now(timezone.utc) + __import__("datetime").timedelta(hours=8)
    c.recalculate = lambda: None
    assert not c.soc_sensor_ok(car) and c.car_soc(car) is None
    c.set_manual_soc("ev", 35)
    assert c.car_soc(car) == 35.0
    states["sensor.bil_soc"] = State("62")
    assert c.soc_sensor_ok(car) and c.car_soc(car) == 62.0
    c._track_soc()
    assert "ev" not in c.manual_soc


def test_ev_from_grid_keeps_house_battery():
    """Lader elbilen fra nettet, gemmes husbatteriet i stedet for at blive tømt ned i bilen."""
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 5, 2, 0, tzinfo=tz).astimezone(timezone.utc)
    sl = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 1.0, 1.0, 0.5) for i in range(4)]
    st = {"sensor.lader": State("7400", unit_of_measurement="W")}
    c, h = make({"battery_growatt_device": "gw1", "battery_growatt_type": "sph",
                 "cars": [{"id": "ev1", "name": "Bil", "power": "sensor.lader"}]}, st)
    c.cars = [{"id": "ev1", "name": "Bil", "power": "sensor.lader"}]
    c.enabled = {"master": True, "battery": True, "ev1": True}
    c.ev_solar = {}
    c.plan = planner.Plan(current=sl[1], battery_action="normal")
    asyncio.run(c._apply_battery_step(c.plan, False))
    assert c.plan.battery_action == "hold" and c.ev_protect
    assert c._script_state.get("battery", "").startswith("hold@")
    # Bilen er fuld (trækker intet): normal drift igen
    st["sensor.lader"] = State("0", unit_of_measurement="W")
    c.plan = planner.Plan(current=sl[2], battery_action="normal")
    c._last_cmd.clear()
    asyncio.run(c._apply_battery_step(c.plan, False))
    assert c.plan.battery_action == "normal" and not c.ev_protect
    # Slået fra for bilen: batteriet bruges som normalt
    st["sensor.lader"] = State("7400", unit_of_measurement="W")
    c.flags["ev1_keep_battery"] = False
    c.plan = planner.Plan(current=sl[2], battery_action="normal")
    asyncio.run(c._apply_battery_step(c.plan, False))
    assert c.plan.battery_action == "normal" and not c.ev_protect
    c.flags["ev1_keep_battery"] = True
    # Solcelleladning: batteriet må gerne bruges som normalt
    st["sensor.lader"] = State("4000", unit_of_measurement="W")
    c.ev_solar = {"ev1": types.SimpleNamespace(active=True)}
    c.plan = planner.Plan(current=sl[3], battery_action="normal")
    asyncio.run(c._apply_battery_step(c.plan, False))
    assert c.plan.battery_action == "normal"


def test_huawei_hold_sets_max_discharge_power_to_zero():
    reg_entries = {"number.bat_dis": types.SimpleNamespace(entity_id="number.bat_dis", platform="huawei_solar",
                                                           domain="number", unique_id="HV123_storage_maximum_discharging_power",
                                                           disabled_by=None, device_id="bat")}
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda hass: types.SimpleNamespace(entities=reg_entries)
    sys.modules["homeassistant.helpers.entity_registry"] = er_mod
    sys.modules["homeassistant.helpers"].entity_registry = er_mod
    st = {"number.bat_dis": State("5000", max=5000)}
    c, h = make({"battery_huawei_device": "bat"}, st)
    assert c.has_hold
    asyncio.run(c._apply_battery("hold", False))
    calls = [(x[0], x[1], x[2].get("value")) for x in h.services.calls]
    assert ("number", "set_value", 0) in calls and ("huawei_solar", "stop_forcible_charge", None) in calls
    st["number.bat_dis"] = State("0", max=5000)
    c._last_cmd.clear()
    asyncio.run(c._apply_battery("normal", False))
    assert ("number", "set_value", 5000) in [(x[0], x[1], x[2].get("value")) for x in h.services.calls]
    # Uden tallet: ingen "gem" (som før)
    c2, h2 = make({"battery_huawei_device": "bat"}, {})
    reg_entries.clear()
    assert not c2.has_hold


def test_full_current_when_charging_from_grid():
    """Lader bilen fra nettet, bruges laderens fulde strøm – ikke solcelleladningens maks."""
    st = {"number.amp": State("10", max=32)}
    c, h = make({}, st)
    assert c.full_current({"id": "ev", "easee_device": "e1"}) == 32
    assert c.full_current({"id": "ev", "current_entity": "number.amp"}) == 32
    c.settings["ev_solar_max_a"] = 13
    assert c.full_current({"id": "ev", "current_script": "script.x"}) in (13, 16)


def test_charge_fault_warning_until_unplugged():
    """Lader bilen fra nettet med under 80 % af ladeeffekten i 5 min (fx en fase faldet ud): ladefejl,
    som først forsvinder, når bilen tages ud af laderen."""
    st = {"sensor.lader": State("6800", unit_of_measurement="W")}
    c, h = make({}, st)
    c.cars = [{"id": "ev", "name": "Bil", "power": "sensor.lader"}]
    c.settings["ev_charge_power"] = 10.2
    c.ev_solar = {}
    c.connected = {"ev": True}
    c.car_soc = lambda car: 50
    c.plan = planner.Plan(evs={"ev": types.SimpleNamespace(on=True)})
    clock = [0.0]
    real = ctl_mod.time.monotonic
    ctl_mod.time.monotonic = lambda: clock[0]
    try:
        c._track_fault(); assert "ev" not in c.ev_fault
        clock[0] = 299; c._track_fault(); assert "ev" not in c.ev_fault
        clock[0] = 301; c._track_fault(); assert c.ev_fault["ev"]["kw"] == 6.8
        st["sensor.lader"] = State("10200", unit_of_measurement="W")   # bliver stående, selv om strømmen kommer igen
        clock[0] = 400; c._track_fault(); assert "ev" in c.ev_fault
        c.connected = {"ev": False}; c._track_fault(); assert "ev" not in c.ev_fault
        # Solcelleladning eller over 90 %: ingen fejl
        c.connected = {"ev": True}; st["sensor.lader"] = State("4000", unit_of_measurement="W")
        c.ev_solar = {"ev": types.SimpleNamespace(active=True)}
        clock[0] = 1000; c._track_fault(); clock[0] = 1400; c._track_fault(); assert "ev" not in c.ev_fault
        c.ev_solar = {}; c.car_soc = lambda car: 95
        clock[0] = 2000; c._track_fault(); clock[0] = 2400; c._track_fault(); assert "ev" not in c.ev_fault
    finally:
        ctl_mod.time.monotonic = real

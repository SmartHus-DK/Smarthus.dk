"""Test af planlægning, prismodel og besparelser (kører uden Home Assistant).

Kør:  python -m pytest tests
"""

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PKG = Path(__file__).parents[1] / "custom_components" / "smarthus"
pkg = types.ModuleType("smarthus")
pkg.__path__ = [str(PKG)]
sys.modules["smarthus"] = pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"smarthus.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"smarthus.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("const")
planner = _load("planner")
pricing = _load("pricing")
savings = _load("savings")
profile = _load("profile")
weather = _load("weather")

TZ = ZoneInfo("Europe/Copenhagen")


def records(day: str, prices_per_hour: list[float]) -> list[dict]:
    """15-minutters poster for en lokal dag med givne timepriser (kr/kWh spot)."""
    start = datetime.fromisoformat(day).replace(tzinfo=TZ).astimezone(timezone.utc)
    out = []
    for h, p in enumerate(prices_per_hour):
        for q in range(4):
            t = start + timedelta(hours=h, minutes=15 * q)
            out.append({"TimeUTC": t.strftime("%Y-%m-%dT%H:%M:%S"), "DayAheadPriceDKK": p * 1000})
    return out


CURVE = [0.3, 0.2, 0.1, 0.1, 0.2, 0.4, 0.8, 1.0, 0.9, 0.7, 0.5, 0.4,
         0.3, 0.3, 0.4, 0.6, 0.9, 1.4, 1.6, 1.2, 0.8, 0.6, 0.5, 0.4]


def model():
    return pricing.PriceModel(dict(const.DEFAULT_PRICES), TZ)


def test_price_model_tariffs_and_vat():
    m = model()
    winter_peak = datetime(2026, 1, 10, 18, tzinfo=TZ)
    p = m.buy_price(1.0, winter_peak)
    expected = (1.0 + 0.05 + 1.17 + 0.14 + 0.008) * 1.25
    assert abs(p - expected) < 1e-3
    assert m.net_tariff(datetime(2026, 7, 1, 3, tzinfo=TZ)) == 0.13
    assert m.sale_price(1.0) == 0.97


def test_build_slots_resolution():
    slots = model().build_slots(records("2026-10-18", CURVE))
    assert len(slots) == 96
    assert slots[0].hours == 0.25


def test_cheapest_and_contiguous():
    slots = model().build_slots(records("2026-10-18", CURVE))
    ch = planner.cheapest(slots, 2)
    assert len(ch) == 8
    hrs = {s.start.astimezone(TZ).hour for s in ch}
    assert hrs == {2, 3}
    block = planner.cheapest(slots, 3, contiguous=True)
    assert len(block) == 12
    assert block[0].start.astimezone(TZ).hour == 1
    exp = planner.cheapest(slots, 2, most_expensive=True)
    assert {s.start.astimezone(TZ).hour for s in exp} == {17, 18}


def test_build_plan_actions():
    slots = model().build_slots(records("2026-10-18", CURVE))
    s = dict(const.DEFAULT_SETTINGS)
    now = datetime(2026, 10, 18, 2, 30, tzinfo=TZ).astimezone(timezone.utc)
    p = planner.build_plan(slots, now, TZ, s, soc=50)
    assert p.battery_action == "charge"
    assert p.heating_mode == "boost"
    assert p.cheap_now
    assert p.hotwater_on
    now = datetime(2026, 10, 18, 18, 10, tzinfo=TZ).astimezone(timezone.utc)
    p = planner.build_plan(slots, now, TZ, s, soc=50)
    assert p.battery_action == "discharge"
    assert p.heating_mode == "reduce"
    assert p.expensive_now
    # Lavt batteri: ingen afladning
    p = planner.build_plan(slots, now, TZ, s, soc=10)
    assert p.battery_action == "normal"


def test_battery_spread_blocks_flat_prices():
    slots = model().build_slots(records("2026-10-18", [0.5] * 24))
    s = dict(const.DEFAULT_SETTINGS)
    s["battery_min_spread"] = 5.0
    p = planner.build_plan(slots, slots[10].start, TZ, s)
    assert p.battery_charge == [] and p.battery_action == "normal"


def test_ev_window_and_fallback():
    recs = records("2026-10-18", CURVE) + records("2026-10-19", CURVE)
    slots = model().build_slots(recs)
    now = datetime(2026, 10, 18, 20, 0, tzinfo=TZ).astimezone(timezone.utc)
    ev = planner.ev_plan(slots, now, TZ, 4, 7)
    assert len(ev) == 16
    for sl in ev:
        lt = sl.start.astimezone(TZ)
        assert datetime(2026, 10, 18, 7, tzinfo=TZ) <= lt < datetime(2026, 10, 19, 7, tzinfo=TZ)
    # Kun i dag kendt kl. 10 -> vent
    only_today = model().build_slots(records("2026-10-18", CURVE))
    now = datetime(2026, 10, 18, 10, 0, tzinfo=TZ).astimezone(timezone.utc)
    assert planner.ev_plan(only_today, now, TZ, 4, 7) == []
    # Tæt på klar-tid uden data -> lad (sikkerhed)
    now = datetime(2026, 10, 19, 3, 30, tzinfo=TZ).astimezone(timezone.utc)
    assert planner.ev_plan(only_today, now, TZ, 4, 7)


def test_dst_day_23_hours():
    # 25. oktober 2026: sommertid slutter (25 timer). Prøv blot at det ikke fejler.
    start = datetime(2026, 10, 24, 22, tzinfo=timezone.utc)
    recs = [{"TimeUTC": (start + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%S"),
             "DayAheadPriceDKK": 500 + i} for i in range(100)]
    slots = model().build_slots(recs)
    days = planner.local_days(slots, TZ)
    assert len(days["2026-10-25"]) == 100
    p = planner.build_plan(slots, slots[5].start, TZ, dict(const.DEFAULT_SETTINGS))
    assert p.current == slots[5]


def test_savings():
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    cheap = slots[10]
    now_local = cheap.start.astimezone(TZ)
    avg = planner.avg(slots)
    calc.update(now_local, cheap, avg, {"ev": 100.0, "battery_charge": 10.0, "battery_discharge": 5.0})
    calc.update(now_local, cheap, avg, {"ev": 101.0, "battery_charge": 11.0, "battery_discharge": 5.0})
    assert abs(calc.value("today", "ev") - round(avg - cheap.price, 2)) < 0.011
    assert calc.value("today", "battery") < 0
    # Måler nulstillet -> ingen negativ besparelse
    calc.update(now_local, cheap, avg, {"ev": 0.0})
    assert calc.value("today", "ev") > 0
    # Ny dag nulstiller "i dag", men ikke "i alt"
    calc.update(now_local + timedelta(days=1), cheap, avg, {})
    assert calc.value("today") == 0
    assert calc.value("total") != 0


def test_solar_overview():
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    s = slots[48]
    now_local = s.start.astimezone(TZ)
    avg = planner.avg(slots)
    calc.update(now_local, s, avg, {"solar": 1000.0, "export": 500.0})
    calc.update(now_local, s, avg, {"solar": 1003.0, "export": 501.0})  # 3 kWh produceret, 1 solgt
    assert calc.solar("today", "produced") == 3.0
    assert calc.solar("today", "self_kwh") == 2.0
    assert calc.solar("today", "export_kwh") == 1.0
    assert abs(calc.solar("today", "self_value") - round(2 * s.price, 2)) < 0.011
    assert abs(calc.solar("today", "export_income") - round(s.sale, 2)) < 0.011
    assert calc.solar("year", "produced") == 3.0
    # Solceller tæller ikke med i SmartHus-besparelsen
    assert calc.value("today") == 0
    hist = calc.solar_history()
    assert hist["days"][0]["produced"] == 3.0 and hist["months"][0]["period"] == now_local.strftime("%Y-%m")
    # Nyt år nulstiller "i år", men ikke "i alt"
    calc.update(now_local.replace(year=now_local.year + 1), s, avg, {})
    assert calc.solar("year", "produced") == 0 and calc.solar("total", "produced") == 3.0


def test_savings_migration():
    old = {"day": "2026-10-18", "month": "2026-10", "last": {},
           "today": {"battery": 1.0, "solar": 4.0, "ev": 2.0, "heatpump": 0.0},
           "month_values": {"battery": 1.0, "solar": 40.0, "ev": 2.0, "heatpump": 0.0},
           "total": {"battery": 1.0, "solar": 400.0, "ev": 2.0, "heatpump": 0.0}}
    calc = savings.SavingsCalculator(old)
    assert calc.value("total") == 3.0
    assert calc.solar("total") == 400.0 and calc.solar("month") == 40.0 and calc.solar("year") == 40.0
    assert calc.value("month_values", "hotwater") == 0


def test_merge_periods():
    slots = model().build_slots(records("2026-10-18", CURVE))
    ch = planner.cheapest(slots, 2)
    assert planner.merge_periods(ch, TZ) == ["18/10 02:00–04:00"]


def test_ev_target_soc():
    recs = records("2026-10-18", CURVE) + records("2026-10-19", CURVE)
    slots = model().build_slots(recs)
    s = dict(const.DEFAULT_SETTINGS)  # mål 80 %, 60 kWh, 11 kW
    now = datetime(2026, 10, 18, 20, 0, tzinfo=TZ).astimezone(timezone.utc)
    p = planner.build_plan(slots, now, TZ, s, ev_soc=50)
    need = 0.30 * 60 / 11 / 0.9
    assert abs(p.ev_hours_needed - round(need, 2)) < 0.01
    assert len(p.ev_slots) == round(need / 0.25)
    assert all(sl.end > now for sl in p.ev_slots)
    # Mål nået: ingen opladning, heller ikke med "Lad nu"
    p = planner.build_plan(slots, now, TZ, s, ev_soc=81, ev_force=True)
    assert p.ev_target_reached and not p.ev_on and p.ev_slots == []
    # Lad nu under målet
    p = planner.build_plan(slots, now, TZ, s, ev_soc=40, ev_force=True)
    assert p.ev_on


def test_ev_cost_compare():
    recs = records("2026-10-18", CURVE) + records("2026-10-19", CURVE)
    slots = model().build_slots(recs)
    s = dict(const.DEFAULT_SETTINGS)
    now = datetime(2026, 10, 18, 17, 0, tzinfo=TZ).astimezone(timezone.utc)  # dyr spidslast
    p = planner.build_plan(slots, now, TZ, s, ev_soc=50)
    energy = 0.30 * 60 / 0.9
    assert abs(p.ev_energy - round(energy, 2)) < 0.01
    assert p.ev_cost_now is not None and p.ev_cost_planned is not None
    assert p.ev_cost_now > p.ev_cost_planned > 0
    p = planner.build_plan(slots, now, TZ, s, ev_soc=90)
    assert p.ev_cost_now == 0 and p.ev_cost_planned == 0


def test_time_windows():
    slots = model().build_slots(records("2026-10-18", CURVE))
    s = dict(const.DEFAULT_SETTINGS)
    # Varmt vand kun 05–09: billigste blok skal ligge der
    s["hotwater_from"], s["hotwater_to"] = 5, 9
    # Varme må kun hæves 10–16
    s["heating_boost_from"], s["heating_boost_to"] = 10, 16
    # Batteri aflader kun 17–20, lader 22–06 (over midnat)
    s["battery_discharge_from"], s["battery_discharge_to"] = 17, 20
    s["battery_charge_from"], s["battery_charge_to"] = 22, 6
    p = planner.build_plan(slots, slots[0].start, TZ, s)
    hrs = lambda xs: {x.start.astimezone(TZ).hour for x in xs}
    assert hrs(p.hotwater_slots) <= {5, 6, 7, 8}
    assert hrs(p.heating_boost) <= set(range(10, 16))
    assert hrs(p.battery_discharge) <= {17, 18, 19}
    assert hrs(p.battery_charge) <= {22, 23, 0, 1, 2, 3, 4, 5}
    assert planner.in_window(slots, TZ, 0, 24) == slots


def test_two_cars_and_power_limit():
    recs = records("2026-10-18", CURVE) + records("2026-10-19", CURVE)
    slots = model().build_slots(recs)
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_tesla_target_soc": 90, "ev_tesla_ready_hour": 6, "ev_tesla_battery_capacity": 75,
              "ev_tesla_charge_power": 11})
    now = datetime(2026, 10, 18, 20, 0, tzinfo=TZ).astimezone(timezone.utc)
    cars = [planner.CarInput("ev", "Kia", soc=50), planner.CarInput("ev_tesla", "Tesla", soc=40)]
    p = planner.build_plan(slots, now, TZ, s, cars=cars)
    assert list(p.evs) == ["ev", "ev_tesla"]
    a, b = p.evs["ev"], p.evs["ev_tesla"]
    assert a.slots and b.slots and b.hours_needed > a.hours_needed
    # Uden grænse må de gerne overlappe
    assert {x.start for x in a.slots} & {x.start for x in b.slots}
    # Med 11 kW samlet: aldrig to biler i samme tidsrum, Tesla (klar kl. 6) planlægges først
    s["ev_max_total_power"] = 11
    p = planner.build_plan(slots, now, TZ, s, cars=cars)
    a, b = p.evs["ev"], p.evs["ev_tesla"]
    assert not ({x.start for x in a.slots} & {x.start for x in b.slots})
    assert abs(sum(x.hours for x in b.slots) - round(b.hours_needed / 0.25) * 0.25) < 0.01
    assert a.cost_planned >= 0 and b.cost_planned >= 0
    # Bagudkompatible genveje peger på første bil
    assert p.ev_slots == a.slots


def test_get_cars_legacy():
    cfg = {"ev_charger_entity": "switch.lader", "ev_soc_sensor": "sensor.soc"}
    cars = const.get_cars(cfg)
    assert cars == [{"id": "ev", "name": "Elbil", "charger": "switch.lader", "soc": "sensor.soc", "energy": None}]
    cfg = {"evs": [{"id": "ev", "name": "Kia", "charger": "switch.a"}, {"id": "ev_tesla", "name": "Tesla", "charger": "switch.b"}]}
    assert [c["id"] for c in const.get_cars(cfg)] == ["ev", "ev_tesla"]


def test_stromligning_prices():
    slots = model().build_slots(records("2026-10-18", CURVE))
    # Hele timer fra Strømligning (kr/kWh inkl. alt) – dækker kun de første 12 timer
    base = datetime(2026, 10, 18, 0, 0, tzinfo=TZ)
    raw = [{"start": (base + timedelta(hours=h)).isoformat(), "price": 1.0 + h / 10} for h in range(12)]
    prices = pricing.parse_price_list(raw)
    out, hits = pricing.apply_external_prices(slots, prices)
    assert hits == 48 and len(out) == 96
    assert out[4 * 5 + 2].price == 1.5          # kl. 05:30 -> timepris kl. 05
    assert out[4 * 20].price == slots[4 * 20].price  # kl. 20 uden ekstern pris -> beregnet pris
    assert out[0].sale == slots[0].sale          # salgspris uændret
    # øre/kWh omregnes
    assert pricing.parse_price_list([{"start": base.isoformat(), "price": 215}])[0][1] == 2.15
    assert pricing.parse_price_list([{"start": "ugyldig", "price": 1}, {"x": 1}]) == []


def test_stromligning_spot_for_sale():
    slots = model().build_slots(records("2026-10-18", CURVE))
    base = datetime(2026, 10, 18, 0, 0, tzinfo=TZ)
    spot_vat = pricing.parse_price_list([{"start": (base + timedelta(hours=h)).isoformat(), "price": 1.25} for h in range(24)])
    out, hits = pricing.apply_external_spot(slots, spot_vat, True, 0.03)
    assert hits == 96
    assert out[10].spot == 1.0 and out[10].sale == 0.97
    assert out[10].price == slots[10].price  # købsprisen røres ikke


def test_profile_and_reserve():
    # 7 dage: 0,5 kWh/time om natten, 2 kWh/time kl. 17-21, 1 kWh ellers
    start = datetime(2026, 10, 12, tzinfo=TZ)  # mandag
    hours = []
    for h in range(7 * 24):
        t = start + timedelta(hours=h)
        kwh = 2.0 if 17 <= t.hour < 21 else 0.5 if t.hour < 6 else 1.0
        hours.append((t, kwh))
    hours.append((start, 999.0))  # urealistisk time ignoreres
    p = profile.build_profile(hours)
    assert p.days == 7 and p.ready
    assert abs(p.weekday[18] - 2.0) < 1e-9 and abs(p.weekend[3] - 0.5) < 1e-9
    now = datetime(2026, 10, 19, 16, 30, tzinfo=TZ)  # mandag 16:30
    until = datetime(2026, 10, 20, 2, 0, tzinfo=TZ)  # billigt igen kl. 02
    load = profile.expected_load(p, now, until)
    # 0,5 (16:30-17) + 4×2 + 3×1 (21-24) + 2×0,5 = 12,5
    assert abs(load - 12.5) < 1e-9
    r = profile.calc_reserve(p, now, until, soc=80, capacity=20, min_soc=10, solar_remaining=None)
    assert abs(r.reserve_kwh - 12.5 / profile.EFFICIENCY) < 1e-9
    # 16 kWh i batteriet − 2 kWh laveste niveau − reserve
    assert abs(r.sellable_kwh - (16 - 2 - 12.5 / profile.EFFICIENCY)) < 1e-9
    # Sol trækker fra reserven
    r2 = profile.calc_reserve(p, now, until, soc=80, capacity=20, min_soc=10, solar_remaining=2.0)
    assert r2.solar_kwh > 0 and r2.reserve_kwh < r.reserve_kwh
    # Tomt batteri: intet at sælge
    r3 = profile.calc_reserve(p, now, until, soc=30, capacity=20, min_soc=10, solar_remaining=None)
    assert r3.sellable_kwh == 0
    # For lidt historik
    assert not profile.build_profile(hours[:30]).ready


def test_export_min_price():
    slots = model().build_slots(records("2026-10-18", CURVE))
    s = dict(const.DEFAULT_SETTINGS)
    now = slots[4 * 2].start  # kl. 02: spot 0,10 -> salgspris 0,07
    assert not planner.build_plan(slots, now, TZ, s).solar_curtail  # standard 0 kr: sælg
    s["solar_export_min_price"] = 0.20
    p = planner.build_plan(slots, now, TZ, s, soc=50)
    assert p.solar_curtail and p.battery_action == "charge"
    later = slots[4 * 18].start  # kl. 18: høj pris -> eksport kører igen
    assert not planner.build_plan(slots, later, TZ, s).solar_curtail


def test_ev_solar_regulation():
    solar = _load("solar")
    st = solar.EvSolarState()
    # Under 4 kW sol: start ikke
    solar.regulate_ev(st, 1.0, 3.0, 6, 16, 4.0)
    assert not st.active
    # 2 × 30 s med nok sol: start på mindste strøm
    solar.regulate_ev(st, 1.0, 5.0, 6, 16, 4.0); solar.regulate_ev(st, 1.0, 5.0, 6, 16, 4.0)
    assert st.active and st.current == 6
    solar.regulate_ev(st, 0.8, 5.0, 6, 16, 4.0); assert st.current == 7      # eksport > 0,5: op
    solar.regulate_ev(st, 0.2, 5.0, 6, 16, 4.0); assert st.current == 7      # lille eksport: hold
    solar.regulate_ev(st, -0.6, 5.0, 6, 16, 4.0); assert st.current == 6     # køber: ned
    for _ in range(20):
        solar.regulate_ev(st, 3.0, 9.0, 6, 16, 4.0)
    assert st.current == 16                                                   # aldrig over maks.
    # Solen forsvinder: stop efter 6 × 30 s på laveste strøm med køb
    st.current = 6
    for _ in range(6):
        solar.regulate_ev(st, -1.0, 1.0, 6, 16, 4.0)
    assert not st.active and st.current is None
    # Uden solsensor: start på eksport
    st = solar.EvSolarState()
    solar.regulate_ev(st, 4.5, None, 6, 16, 4.0); solar.regulate_ev(st, 4.5, None, 6, 16, 4.0)
    assert st.active


def test_plug_and_connected_car():
    plug = _load("plug")
    assert plug.plug_state("on") is True and plug.plug_state("off") is False
    assert plug.plug_state("Connected") is True and plug.plug_state("Disconnected") is False
    assert plug.plug_state("charging") is True and plug.plug_state("unavailable") is None
    assert plug.plug_state(None) is None
    ids = ["ev", "ev_tesla"]
    assert plug.pick_connected(ids, {"ev": False, "ev_tesla": True}, None) == "ev_tesla"    # sensor vinder
    assert plug.pick_connected(ids, {"ev": None, "ev_tesla": None}, "ev_tesla") == "ev_tesla"  # manuelt valg
    assert plug.pick_connected(ids, {"ev": None, "ev_tesla": None}, None) is None            # ikke valgt: ingen
    assert plug.pick_connected(ids, {"ev": None, "ev_tesla": None}, "none") is None          # "Ingen bil"
    assert plug.pick_connected(ids, {"ev": None, "ev_tesla": False}, None) == "ev"           # kun én mulig
    assert plug.pick_connected(ids, {"ev": False, "ev_tesla": None}, "ev") == "ev_tesla"     # ev er ikke tilsluttet
    assert plug.pick_connected(ids, {"ev": False, "ev_tesla": False}, "ev") is None          # ingen bil
    # Ikke-tilsluttet bil planlægges ikke
    recs = records("2026-10-18", CURVE) + records("2026-10-19", CURVE)
    slots = model().build_slots(recs)
    now = datetime(2026, 10, 18, 20, 0, tzinfo=TZ).astimezone(timezone.utc)
    cars = [planner.CarInput("ev", "Kia", soc=50, connected=False), planner.CarInput("ev_tesla", "Tesla", soc=40)]
    p = planner.build_plan(slots, now, TZ, dict(const.DEFAULT_SETTINGS), cars=cars)
    assert not p.evs["ev"].connected and p.evs["ev"].slots == [] and not p.evs["ev"].on
    assert p.evs["ev_tesla"].slots


def test_energy_flow():
    flow = _load("flow")
    assert flow.to_kw(2500, "W") == 2.5 and flow.to_kw(2.5, "kW") == 2.5
    f = flow.energy_flow(solar_kw=5.6, export_kw=1.4, battery_kw=-1.2, ev_kw={"ev": 1.4})
    assert f["house_kw"] == 1.6 and f["grid_kw"] == -1.4 and f["battery_kw"] == -1.2
    f = flow.energy_flow(solar_kw=None, export_kw=-2.0, battery_kw=None, ev_kw={})
    assert f["house_kw"] == 2.0 and f["solar_kw"] is None


def test_translations_have_no_html():
    """hassfest afviser tekster med <...> – tjek det her, før det når GitHub."""
    import json
    import re
    for lang in ("da", "en"):
        text = (PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8")
        json.loads(text)
        assert not re.findall(r"<[^>]*>", text), lang


def test_heating_outdoor_ok():
    ok = planner.heating_outdoor_ok
    assert ok(5, 10, False) is True
    assert ok(10, 10, True) is False
    assert ok(9.5, 10, False) is False and ok(9.5, 10, True) is True
    assert ok(8.9, 10, False) is True


def test_pick_weather_entity():
    assert weather.pick_weather_entity(["weather.x", "weather.forecast_home"]) == "weather.forecast_home"
    assert weather.pick_weather_entity(["weather.b", "weather.a", "sensor.t"]) == "weather.a"
    assert weather.pick_weather_entity([]) is None


def test_forecast_sensor_ids():
    ex = {"sensor.energy_production_today", "sensor.energy_production_today_remaining",
          "sensor.energy_production_tomorrow", "sensor.energy_production_today_2"}
    ids = weather.forecast_sensor_ids(None, ex)
    assert ids["today"] == "sensor.energy_production_today" and ids["tomorrow"] == "sensor.energy_production_tomorrow"
    ids = weather.forecast_sensor_ids("sensor.energy_production_today_remaining_2", ex)
    assert ids == {"today": "sensor.energy_production_today_2", "remaining": "sensor.energy_production_today_remaining_2"}
    sc = {"sensor.solcast_pv_forecast_forecast_today", "sensor.solcast_pv_forecast_forecast_remaining_today"}
    ids = weather.forecast_sensor_ids("sensor.solcast_pv_forecast_forecast_remaining_today", sc)
    assert ids["today"] == "sensor.solcast_pv_forecast_forecast_today"
    assert weather.forecast_sensor_ids("sensor.min_egen", set()) == {"remaining": "sensor.min_egen"}


def test_summarize_forecast():
    day = datetime(2026, 10, 19, tzinfo=TZ)
    # Solcast-stil: nøgle = periodens start, 1 kWh pr. time 08–16
    wh = {(day + timedelta(hours=h)).isoformat(): 1000.0 for h in range(8, 17)}
    wh[(day + timedelta(days=1, hours=12)).isoformat()] = 2500.0
    now = (day + timedelta(hours=12, minutes=30)).astimezone(timezone.utc)
    f = weather.summarize_forecast(wh, now, TZ)
    assert f["today_kwh"] == 9.0 and f["tomorrow_kwh"] == 2.5
    assert f["remaining_kwh"] == 4.5          # halvdelen af 12-timen + 13–16
    assert len(f["hours"]) == 9 and f["hours"][0]["kw"] == 1.0
    # Forecast.Solar-stil: nøgle = periodens slutning, første punkt er solopgang
    fs = {(day + timedelta(hours=7, minutes=52)).isoformat(): 0, (day + timedelta(hours=8)).isoformat(): 40,
          (day + timedelta(hours=9)).isoformat(): 600, (day + timedelta(hours=10)).isoformat(): 1200}
    f = weather.summarize_forecast(fs, (day + timedelta(hours=8, minutes=30)).astimezone(timezone.utc), TZ, key_is_end=True)
    assert f["today_kwh"] == 1.84 and f["remaining_kwh"] == 1.5
    assert f["peak_kw"] == 1.2 and f["peak_time"].startswith("2026-10-19T09:00")
    assert weather.summarize_forecast({}, now, TZ) == {}


def test_grid_import():
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    s = slots[48]
    now_local = s.start.astimezone(TZ)
    calc.update(now_local, s, None, {"import": 200.0})
    calc.update(now_local, s, None, {"import": 202.5})          # 2,5 kWh købt – også uden solcellemåler
    assert calc.solar("today", "import_kwh") == 2.5 and calc.solar("total", "import_kwh") == 2.5
    assert abs(calc.solar("today", "import_cost") - round(2.5 * s.price, 2)) < 0.011
    assert abs(savings.import_price(calc.data["solar"]["today"]) - s.price) < 0.001
    assert calc.solar("today", "produced") == 0
    assert savings.import_price({"import_kwh": 0, "import_cost": 0}) is None

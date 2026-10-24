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


def test_solar_export_not_lost_when_meters_update_out_of_step():
    """Inverteren tæller i spring, elmåleren løbende – eksporten må ikke gå tabt."""
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    s = slots[48]
    t = s.start.astimezone(TZ)
    avg = planner.avg(slots)
    calc.update(t, s, avg, {"solar": 100.0, "export": 50.0})
    # 4 minutter: eksport tikker løbende, inverteren springer først i sidste minut
    calc.update(t, s, avg, {"solar": 100.0, "export": 50.2})
    calc.update(t, s, avg, {"solar": 100.0, "export": 50.4})
    calc.update(t, s, avg, {"solar": 100.0, "export": 50.6})
    calc.update(t, s, avg, {"solar": 101.0, "export": 50.8})
    assert calc.solar("today", "produced") == 1.0
    assert calc.solar("today", "export_kwh") == 0.8
    assert calc.solar("today", "self_kwh") == 0.2
    # Egetforbrug falder igen, når eksporten "indhenter" produktionen
    calc.update(t, s, avg, {"solar": 101.0, "export": 50.9})
    assert calc.solar("today", "self_kwh") == 0.1 and calc.solar("today", "export_kwh") == 0.9
    # Eksport kan ikke overstige produktionen (fx batteri der sælger)
    calc.update(t, s, avg, {"solar": 101.0, "export": 52.0})
    assert calc.solar("today", "export_kwh") == 1.0 and calc.solar("today", "self_kwh") == 0.0


def test_rebuild_solar_from_history():
    now = datetime(2026, 10, 20, 19, 0, tzinfo=TZ)
    rows = [
        {"start": datetime(2026, 7, 9, 12, tzinfo=TZ), "produced": 5.0, "export": 3.0, "import": 0.5, "price": 2.0, "sale": 1.0},
        {"start": datetime(2026, 10, 19, 12, tzinfo=TZ), "produced": 4.0, "export": 5.0, "import": 1.0, "price": 2.5, "sale": 1.2},
        {"start": datetime(2026, 10, 20, 9, tzinfo=TZ), "produced": 1.0, "export": None, "import": 2.0, "price": None, "sale": None},
    ]
    calc = savings.SavingsCalculator()
    calc.data["last"] = {"solar": 1.0, "ev": 7.0}
    res = calc.rebuild_solar(rows, now, {"solar": 1234.5, "export": 99.0, "import": None})
    assert res["hours"] == 3 and res["hours_without_price"] == 1 and res["from"] == "2026-07-09"
    assert calc.solar("total", "produced") == 10.0
    assert calc.solar("total", "export_kwh") == 7.0          # 3 + min(5, 4) + 0
    assert calc.solar("total", "self_kwh") == 3.0
    assert calc.solar("total", "self_value") == 2 * 2.0 + 0 + 0
    assert calc.solar("total", "export_income") == round(3 * 1.0 + 4 * 1.2, 2)
    assert calc.solar("month", "produced") == 5.0 and calc.solar("today", "produced") == 1.0
    assert calc.solar("year", "produced") == 10.0
    assert [d["period"] for d in calc.solar_history()["days"]] == ["2026-07-09", "2026-10-19", "2026-10-20"]
    assert calc.data["last"] == {"solar": 1234.5, "ev": 7.0, "export": 99.0}
    # Den løbende optælling fortsætter fra historikkens slutning
    s = model().build_slots(records("2026-10-20", CURVE))[76]
    calc.update(now, s, 2.0, {"solar": 1235.5, "export": 99.0})
    assert calc.solar("today", "produced") == 2.0


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


def test_battery_trade():
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    night, noon, evening = slots[12], slots[48], slots[72]
    tz = lambda s: s.start.astimezone(TZ)
    r = {"battery_charge": 100.0, "battery_discharge": 50.0, "export": 10.0}
    calc.update(tz(night), night, None, r, battery_action="charge")
    # 5 kWh fra nettet om natten
    calc.update(tz(night), night, None, {**r, "battery_charge": 105.0}, battery_action="charge")
    # 5 kWh solstrøm midt på dagen (koster den salgspris, man ikke fik)
    calc.update(tz(noon), noon, None, {**r, "battery_charge": 110.0}, battery_action="normal")
    t = calc.trade("today")
    assert t["charged_grid_kwh"] == 5 and t["charged_solar_kwh"] == 5
    expect_avg = (5 * night.price + 5 * max(noon.sale, 0)) / 10
    assert abs(t["avg_cost_in_battery"] - round(expect_avg, 3)) < 0.002
    # Aften: 4,5 kWh ud, heraf 3 kWh solgt til nettet
    calc.update(tz(evening), evening, None, {"battery_charge": 110.0, "battery_discharge": 54.5, "export": 13.0},
                battery_action="discharge")
    t = calc.trade("today")
    assert t["sold_kwh"] == 3 and abs(t["house_kwh"] - 1.5) < 1e-9
    cost_sold = 3 / savings.ROUND_TRIP * expect_avg
    assert abs(t["income"] - round(3 * evening.sale, 2)) < 0.011
    assert abs(t["profit"] - round(3 * evening.sale - cost_sold, 2)) < 0.02
    assert t["cost_grid"] > 0 and t["cost_solar"] >= 0
    assert calc.trade("total")["sold_kwh"] == 3


def _blocks(slots):
    """Sammenhængende blokke af kvarterer: [(start, timer)]."""
    out = []
    for s in sorted(slots, key=lambda x: x.start):
        if out and s.start == out[-1][1]:
            out[-1][1] = s.end
        else:
            out.append([s.start, s.end])
    return [(a, (b - a).total_seconds() / 3600) for a, b in out]


def test_heating_whole_hours():
    # Pris der hopper op og ned hvert kvarter – varmen må ikke følge med
    zig = [0.3, 0.9, 0.2, 1.0, 0.25, 0.95, 0.4, 0.8, 0.35, 1.1, 0.5, 0.6,
           0.7, 0.4, 0.9, 0.3, 1.5, 1.8, 2.0, 1.7, 1.2, 0.9, 0.6, 0.5]
    slots = model().build_slots(records("2026-10-21", zig))
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"heating_boost_hours": 4, "heating_reduce_hours": 3, "heating_reduce_degrees": 1})
    now = slots[0].start
    p = planner.build_plan(slots, now, TZ, s)
    for name, chosen, hours in (("boost", p.heating_boost, 4), ("reduce", p.heating_reduce, 3)):
        blocks = _blocks(chosen)
        assert len(blocks) <= 2, (name, blocks)
        assert sum(h for _, h in blocks) == hours, (name, blocks)
        assert all(h >= 2 or len(blocks) == 1 for _, h in blocks), (name, blocks)
        assert all(a.astimezone(TZ).minute == 0 for a, _ in blocks), (name, blocks)   # hele klokketimer
    assert not set(x.start for x in p.heating_boost) & set(x.start for x in p.heating_reduce)
    # De dyreste timer (kl. 16-20) bliver sparetimerne
    assert {a.astimezone(TZ).hour for a, _ in _blocks(p.heating_reduce)} <= {16, 17, 18}


def test_solar_roi():
    calc = savings.SavingsCalculator()
    now = datetime(2026, 10, 20, 19, 0, tzinfo=TZ)
    rows = [{"start": datetime(2026, 7, 11, 12, tzinfo=TZ), "produced": 1000.0, "export": 500.0,
             "import": 0.0, "price": 2.0, "sale": 1.0}]
    calc.rebuild_solar(rows, now)
    assert savings.roi(calc, None, now) is None and savings.roi(calc, 0, now) is None
    r = savings.roi(calc, 100000, now)
    assert r["earned"] == 1500.0 and r["remaining"] == 98500.0 and r["since"] == "2026-07-11"
    # 11. juli–20. oktober er ca. en tredjedel af et dansk solår
    assert 4000 < r["per_year"] < 4700
    assert r["payback_year"] > 2026 and r["payback_years"] == round(100000 / r["per_year"], 1)
    # For lidt data: ingen tilbagebetalingstid endnu
    calc2 = savings.SavingsCalculator()
    calc2.rebuild_solar([{**rows[0], "start": datetime(2026, 10, 10, 12, tzinfo=TZ)}], now)
    assert savings.roi(calc2, 100000, now)["payback_year"] is None


def test_historic_elafgift():
    m = model()
    assert pricing.elafgift_at(datetime(2024, 6, 1, 12, tzinfo=TZ)) == 0.761
    assert pricing.elafgift_at(datetime(2023, 3, 1, tzinfo=TZ)) == 0.008
    assert pricing.elafgift_at(datetime(2026, 10, 1, tzinfo=TZ)) == 0.008
    assert pricing.elafgift_at(datetime(2030, 1, 1, tzinfo=TZ)) is None
    t = datetime(2025, 3, 1, 12, tzinfo=TZ)
    # Historisk pris bruger datidens afgift; den løbende pris bruger den indstillede
    assert round(m.buy_price(1.0, t, historic=True) - m.buy_price(1.0, t), 4) == round((0.720 - m.c[const.CONF_ELAFGIFT]) * 1.25, 4)


def test_external_vat():
    assert pricing.has_vat("incl", "sensor.hvad_som_helst") and not pricing.has_vat("excl", "sensor.x_vat")
    assert pricing.has_vat(None, "sensor.stromligning_current_price_vat")       # ældre opsætning uden valg
    assert pricing.vat_factor(True, True) == 1.0
    assert pricing.vat_factor(False, True) == 1.25                               # uden moms → læg moms på
    assert abs(pricing.vat_factor(True, False) - 0.8) < 1e-9                     # erhverv uden moms


def test_ev_short_of_time():
    # Kl. 08:57, klar kl. 09, fra 33 % til 80 % – der er kun 3 minutter
    recs = records("2026-10-21", CURVE) + records("2026-10-22", CURVE)
    slots = model().build_slots(recs)
    now = datetime(2026, 10, 21, 8, 57, tzinfo=TZ).astimezone(timezone.utc)
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_target_soc": 80, "ev_ready_hour": 9, "ev_charge_power": 10.2, "ev_battery_capacity": 78})
    p = planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "VW", soc=33)])
    ep = p.evs["ev"]
    assert ep.short_of_time and ep.on
    assert 33 < ep.soc_at_ready < 35                   # 3 min × 10,2 kW ≈ 0,5 kWh ≈ +0,6 %
    assert ep.energy < 1 and ep.cost_now == ep.cost_planned   # ingen misvisende sammenligning
    # Med god tid er der ingen advarsel
    now2 = datetime(2026, 10, 21, 20, 0, tzinfo=TZ).astimezone(timezone.utc)
    p2 = planner.build_plan(slots, now2, TZ, s, cars=[planner.CarInput("ev", "VW", soc=33)])
    assert not p2.evs["ev"].short_of_time


def test_ev_ready_same_day():
    # Kl. 14 med morgendagens priser kendt, klar kl. 22 samme dag → lad i dag før 22
    recs = records("2026-10-21", CURVE) + records("2026-10-22", CURVE)
    slots = model().build_slots(recs)
    now = datetime(2026, 10, 21, 14, 0, tzinfo=TZ).astimezone(timezone.utc)
    ready = datetime(2026, 10, 21, 22, 0, tzinfo=TZ)
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_ready_hour": 22, "ev_target_soc": 80, "ev_charge_power": 11, "ev_battery_capacity": 60, "ev_hours": 2})
    for soc in (50, None):
        ep = planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=soc)]).evs["ev"]
        assert ep.slots, soc
        assert all(now <= x.start and x.end <= ready for x in ep.slots), (soc, ep.slots[0].start)
    # Uden SoC: allerede ladet 1,5 time frem mod kl. 22 → kun en halv time mere
    ep = planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "Bil", charged_h=1.5)]).evs["ev"]
    assert sum(x.hours for x in ep.slots) == 0.5


def test_ev_ready_day_fixed():
    from datetime import date
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_ready_hour": 22, "ev_target_soc": 80, "ev_charge_power": 11, "ev_battery_capacity": 60})
    tomorrow = date(2026, 10, 22)
    # Valgt kl. 9 den 21/10: klar i morgen kl. 22
    now = datetime(2026, 10, 21, 9, 0, tzinfo=TZ).astimezone(timezone.utc)
    assert planner.car_ready(now, TZ, 22, tomorrow) == datetime(2026, 10, 22, 22, 0, tzinfo=TZ)
    assert planner.car_ready(now, TZ, 22, None) == datetime(2026, 10, 21, 22, 0, tzinfo=TZ)   # næste gang = i dag
    # Næste dag kl. 14 kommer nye priser (for 23/10) – målet flytter sig ikke
    now2 = datetime(2026, 10, 22, 14, 0, tzinfo=TZ).astimezone(timezone.utc)
    assert planner.car_ready(now2, TZ, 22, tomorrow) == datetime(2026, 10, 22, 22, 0, tzinfo=TZ)
    recs = records("2026-10-21", CURVE) + records("2026-10-22", CURVE) + records("2026-10-23", CURVE)
    slots = model().build_slots(recs)
    ep = planner.build_plan(slots, now2, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=40, ready_date=tomorrow)]).evs["ev"]
    assert ep.slots and max(x.end for x in ep.slots) <= datetime(2026, 10, 22, 22, 0, tzinfo=TZ)
    # Fra 21/10 kl. 9 med "i morgen" må den gerne bruge nattens billige timer
    ep = planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=40, ready_date=tomorrow)]).evs["ev"]
    assert any(x.start.astimezone(TZ).date() == tomorrow for x in ep.slots)
    # Når tidspunktet er passeret, gælder "næste gang" igen
    now3 = datetime(2026, 10, 22, 22, 30, tzinfo=TZ).astimezone(timezone.utc)
    assert planner.car_ready(now3, TZ, 22, tomorrow) == datetime(2026, 10, 23, 22, 0, tzinfo=TZ)


def test_ev_charge_period():
    # Ingen klar-tid: lad billigst i dag / i morgen / begge dage / alle kendte priser
    recs = records("2026-10-21", CURVE) + records("2026-10-22", [p * 0.5 for p in CURVE])
    slots = model().build_slots(recs)
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_target_soc": 80, "ev_charge_power": 11, "ev_battery_capacity": 60, "ev_ready_hour": 7})
    now = datetime(2026, 10, 21, 9, 0, tzinfo=TZ).astimezone(timezone.utc)
    d21, d22, d23 = (datetime(2026, 10, d, tzinfo=TZ) for d in (21, 22, 23))

    def plan(**kw):
        return planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=50, **kw)]).evs["ev"]

    today = plan(deadline=d22)
    assert today.slots and all(now <= x.start and x.end <= d22 for x in today.slots)
    tomorrow = plan(deadline=d23, earliest=d22)
    assert tomorrow.slots and all(d22 <= x.start and x.end <= d23 for x in tomorrow.slots)
    both = plan(deadline=d23)
    assert all(x.start >= d22 for x in both.slots)          # i morgen er halvt så dyrt → vælges
    horizon = plan(horizon=True)
    assert [x.start for x in horizon.slots] == [x.start for x in both.slots]
    assert not today.short_of_time and not tomorrow.short_of_time
    # Forventet klar = slutningen af den sidste valgte ladetime
    assert tomorrow.done_at == max(x.end for x in tomorrow.slots) and d22 < tomorrow.done_at <= d23


def test_ev_waiting_and_horizon_cost():
    slots_today = model().build_slots(records("2026-10-21", CURVE))     # morgendagens priser ikke kendt
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_target_soc": 80, "ev_charge_power": 11, "ev_battery_capacity": 60, "ev_ready_hour": 0})
    now = datetime(2026, 10, 21, 9, 0, tzinfo=TZ).astimezone(timezone.utc)
    d22, d23 = datetime(2026, 10, 22, tzinfo=TZ), datetime(2026, 10, 23, tzinfo=TZ)
    ep = planner.build_plan(slots_today, now, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=50, deadline=d23, earliest=d22)]).evs["ev"]
    assert not ep.slots and ep.waiting_for_prices and ep.done_at is None
    # Klar kl. 00: midnat i nat – planlægges i dag
    ep = planner.build_plan(slots_today, now, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=50)]).evs["ev"]
    assert ep.ready_at == d22 and ep.slots and not ep.waiting_for_prices
    assert ep.cost_horizon is not None and ep.cost_horizon <= ep.cost_planned + 0.01 <= ep.cost_now + 0.02


energy = _load("energy")


def test_energy_layout_from_energy_dashboard():
    prefs = {
        "energy_sources": [
            {"type": "grid", "flow_from": [{"stat_energy_from": "sensor.imp"}], "flow_to": [{"stat_energy_to": "sensor.exp"}]},
            {"type": "solar", "stat_energy_from": "sensor.pv"},
            {"type": "battery", "stat_energy_from": "sensor.bat_out", "stat_energy_to": "sensor.bat_in"},
        ],
        "device_consumption": [
            {"stat_consumption": "sensor.vp1"},
            {"stat_consumption": "sensor.vp2", "name": "Varmepumpe 2"},
            {"stat_consumption": "sensor.vp2_pumpe", "included_in_stat": "sensor.vp2"},  # underenhed – tælles ikke ekstra
        ],
    }
    lay = energy.layout_from_energy_prefs(prefs, {"sensor.vp1": "Varmepumpe 1"})
    assert lay["import"] == ["sensor.imp"] and lay["export"] == ["sensor.exp"] and lay["solar"] == ["sensor.pv"]
    assert lay["battery_in"] == ["sensor.bat_in"] and lay["battery_out"] == ["sensor.bat_out"]
    assert [d["name"] for d in lay["devices"]] == ["Varmepumpe 1", "Varmepumpe 2"]
    assert energy.layout_from_energy_prefs({"energy_sources": [], "device_consumption": []}) is None
    # Uden Energi-dashboard: SmartHus' egne målere, samme måler kun én gang
    cfg = energy.layout_from_config({"import": "sensor.imp", "solar": None},
                                    [("Varmepumpe", "sensor.vp"), ("Varmt vand", "sensor.vp"), ("Bil", None)])
    assert cfg["import"] == ["sensor.imp"] and cfg["solar"] == [] and [d["name"] for d in cfg["devices"]] == ["Varmepumpe"]


def test_energy_book_hours_periods_and_years():
    lay = {"import": ["i"], "export": ["e"], "solar": ["s"], "battery_in": ["bi"], "battery_out": ["bo"],
           "devices": [{"id": "d1", "name": "VP1", "stat": "v1"}, {"id": "d2", "name": "VP2", "stat": "v2"}]}
    f = energy.hour_fields(lay, {"i": 2, "e": 1, "s": 3, "bi": 0.5, "bo": 0.5, "v1": 1, "v2": 0.5}, 2.0, 0.5)
    assert f["house"] == 4.0 and f["other"] == 2.5          # 2 + 3 − 1 + 0,5 − 0,5 = 4 kWh; 4 − 1,5 = 2,5
    assert f["solar_self"] == 2.0 and f["solar_kr"] == 2.0 * 2 + 1 * 0.5
    assert f["solar_self_kr"] == 4.0 and f["solar_export_kr"] == 0.5
    assert f["grid_in_kr"] == 4.0 and f["grid_out_kr"] == 0.5 and f["bill"] == 3.5 and f["d1_kr"] == 2.0
    now = datetime(2026, 10, 22, 9, 30, tzinfo=TZ)
    rows = []
    for t in (datetime(2025, 6, 1, 12, tzinfo=TZ), datetime(2026, 10, 19, 8, tzinfo=TZ),
              datetime(2026, 10, 22, 7, tzinfo=TZ), datetime(2026, 10, 22, 8, tzinfo=TZ)):
        rows.append({"start": t, "kwh": {"i": 1, "v1": 0.4}, "price": 2.0, "sale": 0.5})
    book = energy.EnergyBook()
    book.apply(lay, rows, datetime(2025, 6, 1, tzinfo=TZ), True, now)
    ov = book.overview(now)
    assert ov["periods"]["today"]["house"] == 2 and ov["periods"]["week"]["house"] == 3
    assert ov["periods"]["month"]["house"] == 3 and ov["periods"]["total"]["house"] == 4
    assert [y["p"] for y in ov["series"]["total"]] == ["2025", "2026"]
    assert ov["periods"]["year"]["house"] == 3 and len(ov["series"]["year"]) == 12 and ov["series"]["year"][9]["house"] == 3
    assert ov["series"]["today"][7]["house"] == 1 and len(ov["series"]["month"]) == 31 and ov["series"]["week"][0]["p"] == "2026-10-19"
    # Løbende opdatering fra starten af måneden: ældre måneder bevares, oktober erstattes (ikke dobbelt)
    book.apply(lay, rows[1:], datetime(2026, 10, 1, tzinfo=TZ), False, now)
    assert book.overview(now)["periods"]["total"]["house"] == 4
    # Andre målere → bygges forfra
    lay2 = {**lay, "devices": lay["devices"][:1]}
    book.apply(lay2, rows[2:], datetime(2026, 10, 1, tzinfo=TZ), False, now)
    assert book.overview(now)["periods"]["total"]["house"] == 2


def test_energy_adds_smarthus_charger():
    lay = energy.layout_from_energy_prefs({"energy_sources": [{"type": "grid", "flow_from": [{"stat_energy_from": "i"}]}],
                                           "device_consumption": [{"stat_consumption": "vp"}, {"stat_consumption": "sub", "included_in_stat": "vp"}]},
                                          {"vp": "Varmepumpe 1"})
    assert lay["devices"][0]["kind"] == "heatpump"
    energy.add_devices(lay, [("Lader · Audi", "ev", "ev"), ("Lader · Kia", "sub", "ev"), ("Lader · Tesla", None, "ev")], {"vp", "sub"})
    assert [(d["name"], d["kind"]) for d in lay["devices"]] == [("Varmepumpe 1", "heatpump"), ("Lader · Audi", "ev")]


def test_energy_charger_not_twice_and_clean_names():
    lay = energy.layout_from_energy_prefs({"energy_sources": [{"type": "grid", "flow_from": [{"stat_energy_from": "i"}]}],
                                           "device_consumption": [{"stat_consumption": "easee"}, {"stat_consumption": "jv"}]},
                                          {"easee": "Enkehøj 25 - Easee Lifetime energy", "jv": "Jordvarme"})
    assert [(d["name"], d["kind"]) for d in lay["devices"]] == [("Enkehøj 25 - Easee", "ev"), ("Jordvarme", "heatpump")]
    energy.add_devices(lay, [("Lader · Audi", "audi_energy", "ev")], {"easee", "jv"})
    assert len(lay["devices"]) == 2          # laderen står allerede i Energi-dashboardet
    assert energy.device_kind("Elevator level") == "device"


def test_heating_preheats_before_expensive_period():
    # Som en almindelig dag: billig nat, billig middag, meget dyrt 17–20
    curve = [0.55, 0.5, 0.45, 0.4, 0.4, 0.45, 0.6, 0.75, 0.8, 0.7, 0.55, 0.4,
             0.35, 0.4, 0.5, 0.6, 0.8, 1.6, 1.7, 1.6, 1.0, 0.8, 0.7, 0.6]
    slots = model().build_slots(records("2026-10-22", curve))
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"heating_boost_hours": 4, "heating_reduce_hours": 3, "heating_reduce_degrees": 1})
    p = planner.build_plan(slots, slots[0].start, TZ, s)
    blocks = [(a.astimezone(TZ).hour, h) for a, h in _blocks(p.heating_boost)]
    reduce = [(a.astimezone(TZ).hour, h) for a, h in _blocks(p.heating_reduce)]
    assert reduce == [(17, 3.0)]
    assert sum(h for _, h in blocks) == 4 and len(blocks) <= 2
    # 2 timer forvarmning inden for 5 timer før kl. 17 – og 2 timer i nattens billigste timer
    assert any(12 <= start and start + h <= 17 for start, h in blocks), blocks
    assert any(start < 7 for start, _ in blocks), blocks
    # Ingen forvarmning, når timerne før ikke er billigere end sparetimerne
    flat = [1.0] * 24
    flat[3] = 0.2
    p = planner.build_plan(model().build_slots(records("2026-10-22", flat)), slots[0].start, TZ, s)
    assert all(a.astimezone(TZ).hour < 17 for a, _ in _blocks(p.heating_boost))


hotwater = _load("hotwater")


def test_hotwater_cooling_rate_and_demand():
    t0 = datetime(2026, 10, 23, 1, 0, tzinfo=TZ)
    # Opvarmning til 58 °C, derefter 0,5 °C i timen ned
    samples = [(t0 + timedelta(minutes=10 * i), 44 + 3 * i) for i in range(5)]
    peak = samples[-1][0]
    samples += [(peak + timedelta(minutes=10 * i), 56 - 0.5 * i / 6) for i in range(1, 19)]
    now = samples[-1][0]
    rate = hotwater.cooling_rate(samples, now)
    assert abs(rate - 0.5) < 0.02
    assert hotwater.cooling_rate(samples[:6], samples[5][0]) is None          # for kort tid efter toppen
    # Priser: billigt kl. 05, dyrt kl. 07–09
    curve = [1.0] * 24
    curve[5], curve[7], curve[8] = 0.2, 3.0, 3.0
    slots = model().build_slots(records("2026-10-23", curve))
    now = datetime(2026, 10, 23, 3, 0, tzinfo=TZ)
    demand, deadline = hotwater.plan_demand(slots, now, 44.0, 0.5, 45.0, 42.0, 1.0, [])
    assert deadline == now + timedelta(hours=4)
    assert [s.start.astimezone(TZ).hour for s in demand] == [5] * 4             # billigste time før kl. 07
    # Den planlagte opvarmning ligger før – intet ekstra
    assert hotwater.plan_demand(slots, now, 44.0, 0.5, 45.0, 42.0, 1.0, demand)[0] == []
    # Over "hold øje"-grænsen eller funktionen slået fra
    assert hotwater.plan_demand(slots, now, 46.0, 0.5, 45.0, 42.0, 1.0, [])[0] == []
    assert hotwater.plan_demand(slots, now, 44.0, 0.5, 0, 42.0, 1.0, [])[0] == []
    # Allerede under den laveste temperatur: varm nu
    demand, _ = hotwater.plan_demand(slots, now, 41.5, 0.5, 45.0, 42.0, 1.0, [])
    assert demand and demand[0].start <= now < demand[-1].end


def test_heating_two_peaks_preheat_before_both():
    # Dyr morgen og dyr aften, billig middag og sen aften (som 23/10 2026)
    prices = [1.85, 1.75, 1.72, 1.72, 1.68, 1.72, 2.03, 2.25, 2.38, 2.13, 1.83, 1.66,
              1.48, 1.45, 1.48, 1.52, 1.58, 2.13, 2.13, 2.12, 1.75, 1.15, 1.22, 1.1]
    d0 = datetime(2026, 10, 23, tzinfo=TZ)
    slots = [planner.Slot(d0 + timedelta(minutes=15 * i), d0 + timedelta(minutes=15 * (i + 1)), p * 0.4, p, p * 0.3)
             for i, p in enumerate(x for x in prices for _ in range(4))]
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"heating_boost_hours": 6, "heating_reduce_hours": 4, "heating_reduce_degrees": 1})
    p = planner.build_plan(slots, slots[0].start, TZ, s)
    blocks = lambda xs: [(a.astimezone(TZ).hour, h) for a, h in _blocks(xs)]  # noqa: E731
    assert blocks(p.heating_reduce) == [(7, 2.0), (17, 2.0)]      # begge toppe
    boost = blocks(p.heating_boost)
    assert len(boost) == 2 and sum(h for _, h in boost) == 6
    assert any(2 <= a and a + h <= 7 for a, h in boost) and any(12 <= a and a + h <= 17 for a, h in boost), boost


def test_heating_auto_plan_from_prices():
    prices = [1.85, 1.75, 1.72, 1.72, 1.68, 1.72, 2.03, 2.25, 2.38, 2.13, 1.83, 1.66,
              1.48, 1.45, 1.48, 1.52, 1.58, 2.13, 2.13, 2.12, 1.75, 1.15, 1.22, 1.1]
    d0 = datetime(2026, 10, 23, tzinfo=TZ)
    mk = lambda ps: [planner.Slot(d0 + timedelta(minutes=15 * i), d0 + timedelta(minutes=15 * (i + 1)), p * 0.4, p, p * 0.3)  # noqa: E731
                     for i, p in enumerate(x for x in ps for _ in range(4))]
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"heating_auto": True, "heating_reduce_degrees": 1, "heating_boost_hours": 1, "heating_reduce_hours": 1})
    p = planner.build_plan(mk(prices), d0, TZ, s)
    blocks = lambda xs: [(a.astimezone(TZ).hour, h) for a, h in _blocks(xs)]  # noqa: E731
    # Toppene kl. 06–10 og 17–20 bliver sparetimer (timeindstillingerne bruges ikke)
    assert blocks(p.heating_reduce) == [(6, 4.0), (17, 3.0)]
    # Forvarmning før hver top – lige så mange timer som toppen
    assert blocks(p.heating_boost) == [(2, 4.0), (13, 3.0)]
    # En flad dag: ingen plan
    p = planner.build_plan(mk([1.5] * 24), d0, TZ, s)
    assert not p.heating_reduce and not p.heating_boost


def test_ev_charges_in_blocks_of_at_least_one_hour():
    d0 = datetime(2026, 10, 24, tzinfo=TZ)
    # Kvarterpriser: billigt 00–03, men enkelte kvarterer lidt dyrere (giver huller med "billigste kvarterer")
    q = [1.0] * 96
    for i in range(0, 12):
        q[i] = 0.50
    q[2] = q[6] = 0.56          # kl. 00:30 og 01:30 en smule dyrere
    for i in range(12, 16):
        q[i] = 0.58
    slots = [planner.Slot(d0 + timedelta(minutes=15 * i), d0 + timedelta(minutes=15 * (i + 1)), p * 0.4, p, p * 0.3)
             for i, p in enumerate(q)]
    loose = planner.cheapest(slots, 2.5)
    blocks = planner.cheapest_blocks(slots, 2.5)
    assert len(_blocks(loose)) > 1                                  # huller i den gamle plan
    assert len(blocks) == 10 and all(h >= 1 for _, h in _blocks(blocks))
    assert sum(s.price for s in blocks) <= sum(s.price for s in loose) + 0.2   # koster kun lidt mere
    # Under 1 time: én samlet blok
    short = planner.cheapest_blocks(slots, 0.5)
    assert len(_blocks(short)) == 1 and len(short) == 2
    # Elbilens plan bruger blokkene
    s = dict(const.DEFAULT_SETTINGS)
    s.update({"ev_hours": 2.5, "ev_ready_hour": 7})
    now = datetime(2026, 10, 23, 22, 0, tzinfo=TZ)
    ep = planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "Bil", soc=None)]).evs["ev"]
    assert ep.slots and all(h >= 1 for _, h in _blocks(ep.slots))


def test_missing_tomorrow_prices_on_dst_day():
    """Efter kl. 12.30 kigges der efter morgendagens priser – også den dag vi skifter til vintertid (25 timer)."""
    model = pricing.PriceModel(dict(const.DEFAULT_PRICES), TZ)
    day0 = datetime(2026, 10, 24, tzinfo=TZ)                     # i morgen (25/10) har 25 timer
    start_utc = day0.astimezone(timezone.utc)
    recs = [{"TimeUTC": (start_utc + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%S"),
             "DayAheadPriceDKK": 500.0} for i in range(96)]
    slots = model.build_slots(recs)
    assert not pricing.missing_tomorrow(slots, datetime(2026, 10, 24, 11, 0, tzinfo=TZ))   # for tidligt
    assert pricing.missing_tomorrow(slots, datetime(2026, 10, 24, 14, 11, tzinfo=TZ))
    # Morgendagen: 25 timer = 100 kvarterer, fra lokal midnat
    t_utc = datetime(2026, 10, 25, tzinfo=TZ).astimezone(timezone.utc)
    recs += [{"TimeUTC": (t_utc + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%S"),
              "DayAheadPriceDKK": 400.0} for i in range(100)]
    slots = model.build_slots(recs)
    assert not pricing.missing_tomorrow(slots, datetime(2026, 10, 24, 14, 11, tzinfo=TZ))
    days = planner.local_days(slots, TZ)
    assert len(days["2026-10-25"]) == 100

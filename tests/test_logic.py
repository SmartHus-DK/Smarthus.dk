"""Test af planlægning, prismodel og besparelser (kører uden Home Assistant).

Kør:  python -m pytest tests
"""

import importlib.util
import sys
import types
import math
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
    expected = (1.0 + 0.05 + 1.17 + 0.115 + 0.008) * 1.25   # Energinet 2026: 0,115
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


def test_battery_full_charge():
    """"Lad helt op": timerne regnes ud fra ladeeffekten, og der lades uanset prisforskel."""
    assert planner.full_charge_hours(10, 15, 95, 5.0) == 1.75        # 8 kWh / 5 kW / 0,95 = 1,68 t
    assert planner.full_charge_hours(10, 15, 95, 0) is None
    assert planner.full_charge_hours(30, 10, 100, 1.0) == 24.0       # højst et døgn
    slots = model().build_slots(records("2026-10-18", [0.5] * 24))
    s = dict(const.DEFAULT_SETTINGS)
    s["battery_min_spread"] = 5.0
    s["battery_charge_hours"] = 1.75
    s["battery_full_charge"] = True
    p = planner.build_plan(slots, slots[10].start, TZ, s)
    assert abs(sum(x.hours for x in p.battery_charge) - 1.75) < 0.3


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
    assert calc.value("today", "battery") == 0     # opladning bogføres først, når strømmen bruges
    # Måler nulstillet -> ingen negativ besparelse
    calc.update(now_local, cheap, avg, {"ev": 0.0})
    assert calc.value("today", "ev") > 0
    # Ny dag nulstiller "i dag", men ikke "i alt"
    calc.update(now_local + timedelta(days=1), cheap, avg, {})
    assert calc.value("today") == 0
    assert calc.value("total") != 0


def test_battery_savings_solar_charge_costs_sale_price():
    """Besparelsen bogføres, når strømmen bruges: solstrøm koster den mistede salgspris, netstrøm købsprisen."""
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    sl = slots[40]
    t = sl.start.astimezone(TZ)
    calc.update(t, sl, None, {"battery_charge": 10.0, "battery_discharge": 5.0}, battery_action="normal")
    calc.update(t, sl, None, {"battery_charge": 11.0, "battery_discharge": 5.0}, battery_action="normal")
    assert calc.data["today"]["battery"] == 0                     # solen lader: ingen udgift endnu
    # 0,9 kWh ud (1 kWh inkl. tab) til huset: værdi købspris minus den mistede salgspris
    calc.update(t, sl, None, {"battery_charge": 11.0, "battery_discharge": 5.9}, battery_action="normal")
    assert abs(calc.data["today"]["battery"] - (0.9 * sl.price - sl.sale)) < 1e-3
    # Netstrøm: koster købsprisen, når den bruges
    calc2 = savings.SavingsCalculator()
    calc2.update(t, sl, None, {"battery_charge": 10.0, "battery_discharge": 5.0}, battery_action="charge")
    calc2.update(t, sl, None, {"battery_charge": 11.0, "battery_discharge": 5.0}, battery_action="charge")
    assert calc2.data["today"]["battery"] == 0
    dear = max(slots, key=lambda x: x.price)
    calc2.update(dear.start.astimezone(TZ), dear, None, {"battery_charge": 11.0, "battery_discharge": 5.9}, battery_action="normal")
    assert abs(calc2.data["today"]["battery"] - (0.9 * dear.price - sl.price)) < 1e-3


def test_full_charge_cost_counted_separately():
    """Sol solgt, fordi "lad helt op" fyldte batteriet med netstrøm: vises for sig, ikke i besparelsen."""
    slots = model().build_slots(records("2026-10-18", CURVE))
    calc = savings.SavingsCalculator()
    night, day = slots[12], slots[48]
    tn, td = night.start.astimezone(TZ), day.start.astimezone(TZ)
    r = {"battery_charge": 0.0, "battery_discharge": 0.0, "export": 0.0}
    calc.update(tn, night, None, r, battery_action="charge", battery_kwh=0.0, full_charge=True)
    for k in (2.5, 5.0, 7.5, 10.0):
        calc.update(tn, night, None, {**r, "battery_charge": k}, battery_action="charge", battery_kwh=k, full_charge=True)
    # Næste dag: batteriet er fuldt, solen eksporteres 3 kWh
    calc.update(td, day, None, {**r, "battery_charge": 10.0, "export": 3.0}, battery_action="normal",
                battery_kwh=10.0, battery_full=True, full_charge=True)
    tr = calc.trade("today")
    assert tr["full_charge_kwh"] == 3.0
    assert abs(tr["full_charge_cost"] - round(3 * max(night.price - max(day.sale, 0), 0), 2)) < 0.011
    assert calc.data["today"]["battery"] == 0                     # tælles ikke i SmartHus-besparelsen
    # Højst den netstrøm, der blev ladet
    for e in (7.0, 11.0, 15.0, 19.0):
        calc.update(td, day, None, {**r, "battery_charge": 10.0, "export": e}, battery_action="normal",
                    battery_kwh=10.0, battery_full=True, full_charge=True)
    assert calc.trade("today")["full_charge_kwh"] == 10.0
    # Uden "lad helt op" tælles intet
    c2 = savings.SavingsCalculator()
    c2.update(tn, night, None, r, battery_action="charge", battery_kwh=0.0)
    for k in (2.5, 5.0, 7.5, 10.0):
        c2.update(tn, night, None, {**r, "battery_charge": k}, battery_action="charge", battery_kwh=k)
    c2.update(td, day, None, {**r, "battery_charge": 10.0, "export": 3.0}, battery_action="normal", battery_kwh=10.0, battery_full=True)
    assert c2.trade("today")["full_charge_kwh"] == 0


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
    assert sum(x.hours for x in p.ev_slots) == math.ceil(need)            # hele timer, der dækker behovet
    assert all(x.start.astimezone(TZ).minute == 0 for x in p.ev_slots[::4])
    assert all(sl.end > now for sl in p.ev_slots)
    # Mål nået: ingen opladning, heller ikke med "Lad nu"
    p = planner.build_plan(slots, now, TZ, s, ev_soc=81, ev_force=True)
    assert p.ev_target_reached and not p.ev_on and p.ev_slots == []
    # Lad nu under målet
    p = planner.build_plan(slots, now, TZ, s, ev_soc=40, ev_force=True)
    assert p.ev_on


def test_ev_whole_hours_and_running_hour_is_kept():
    """Elbilen lader i hele klokketimer (de billigste, gerne med pauser) – ikke enkelte kvarterer. En time,
    der er begyndt at lade, gøres færdig, også hvis skønnet over batteriniveauet rykker."""
    recs = records("2026-10-18", CURVE) + records("2026-10-19", CURVE)
    slots = model().build_slots(recs)
    s = dict(const.DEFAULT_SETTINGS)
    now = datetime(2026, 10, 18, 19, 5, tzinfo=TZ).astimezone(timezone.utc)
    cur = next(sl for sl in slots if sl.start <= now < sl.end)
    base = planner.plan_cars(slots, now, TZ, s, [planner.CarInput("ev", soc=50)])["ev"]
    assert not base.on                                         # dyr aften: venter på de billige timer
    hours = {}
    for x in base.slots:
        hours.setdefault(x.start.astimezone(TZ).replace(minute=0), []).append(x)
    assert all(len(v) == 4 for v in hours.values())            # kun hele timer
    assert sum(x.hours for x in base.slots) >= base.hours_needed
    run = planner.plan_cars(slots, now, TZ, s, [planner.CarInput("ev", soc=79, was_on_slot=cur.start)])["ev"]
    assert run.on and any(x.start == cur.start for x in run.slots)
    hour = now.astimezone(TZ).replace(minute=0, second=0, microsecond=0)
    assert all(any(x.start == q.start for x in run.slots) for q in slots
               if q.start.astimezone(TZ).replace(minute=0) == hour and q.end > now)   # resten af timen
    done = planner.plan_cars(slots, now, TZ, s, [planner.CarInput("ev", soc=80, was_on_slot=cur.start)])["ev"]
    assert not done.on                                         # mål nået: stop


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
    assert sum(x.hours for x in b.slots) >= b.hours_needed - 0.01
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
    reg = lambda exp, sun: solar.regulate_ev(st, exp, sun, 6, 16, 4.0)  # noqa: E731
    # Under 4 kW sol: start ikke
    reg(1.0, 3.0)
    assert not st.active
    # 2 × 30 s med nok sol: start på mindste strøm
    reg(1.0, 5.0); reg(1.0, 5.0)
    assert st.active and st.current == 6
    # Op først når eksporten har holdt i 2 × 30 s – ingen vippen hvert halve minut
    reg(0.8, 5.0); assert st.current == 6
    reg(0.8, 5.0); assert st.current == 7
    reg(0.2, 5.0); assert st.current == 7                     # lille eksport: hold
    reg(-0.6, 5.0); assert st.current == 7                    # ét køb: vent
    reg(0.8, 5.0); reg(-0.6, 5.0); assert st.current == 7     # skifter frem og tilbage: hold
    reg(-0.6, 5.0); reg(-0.6, 5.0); assert st.current == 6    # køb i 2 × 30 s: ned
    for _ in range(40):
        reg(3.0, 9.0)
    assert st.current == 16                                   # aldrig over maks.
    # Overskuddet rækker ikke til laveste strøm: laderen bliver tændt på laveste strøm
    for _ in range(40):
        reg(-1.0, 1.5)
    assert st.active and st.current == 6
    # Solen er helt væk: stop efter 5 minutter
    for _ in range(9):
        reg(-1.5, 0.05)
    assert st.active
    reg(-1.5, 0.05)
    assert not st.active and st.current is None
    # Uden solsensor: start på eksport – og stop først efter 10 minutters køb på laveste strøm
    st = solar.EvSolarState()
    reg(4.5, None); reg(4.5, None)
    assert st.active
    for _ in range(19):
        reg(-1.0, None)
    assert st.active and st.current == 6
    reg(-1.0, None)
    assert not st.active


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
    assert abs((m.buy_price(1.0, t, historic=True) - m.buy_price(1.0, t)) - (0.720 - m.c[const.CONF_ELAFGIFT]) * 1.25) < 2e-4


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
    # Uden SoC: allerede ladet 1,5 time frem mod kl. 22 → en halv time mere, lagt i én hel time
    # (laderen slukker, når den halve time er ladet)
    ep = planner.build_plan(slots, now, TZ, s, cars=[planner.CarInput("ev", "Bil", charged_h=1.5)]).evs["ev"]
    assert sum(x.hours for x in ep.slots) == 1.0


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


def test_energy_other_balances_over_the_period():
    """Ladeboksens måler kommer en time for sent: rækkerne skal stadig give totalen (Andet regnes for hele perioden)."""
    lay = {"import": ["i"], "devices": [{"id": "d1", "name": "Easee", "stat": "ev"}]}
    now = datetime(2026, 10, 24, 17, 0, tzinfo=TZ)
    rows = [  # kl. 1: huset 10, laderen 0 · kl. 2: huset 2, laderen 10 (forsinket måling)
        {"start": datetime(2026, 10, 24, 1, tzinfo=TZ), "kwh": {"i": 10, "ev": 0}, "price": 1.0, "sale": 0.5},
        {"start": datetime(2026, 10, 24, 2, tzinfo=TZ), "kwh": {"i": 2, "ev": 10}, "price": 1.0, "sale": 0.5},
    ]
    book = energy.EnergyBook()
    book.apply(lay, rows, datetime(2026, 10, 1, tzinfo=TZ), True, now)
    d = book.overview(now)["periods"]["today"]
    assert d["house"] == 12 and d["d1"] == 10
    assert d["other"] == 2 and d["other_kr"] == 2            # ikke 10 (summen af timer, hvor Andet aldrig er negativ)
    assert d["d1"] + d["other"] == d["house"] and d["d1_kr"] + d["other_kr"] == d["house_kr"]


presets = _load("presets")


def _reg(prefix, items, device="dev1"):
    """Falsk entitetsregister: (entity_id, unique_id-nøgle) med integrationens præfiks."""
    return [presets.Entry(eid, f"{prefix}_{key}", device) for eid, key in items]


def test_preset_sigenergy_finds_entities_and_control():
    reg = _reg("01JABC_plant", [
        ("sensor.sigen_plant_battery_state_of_charge", "plant_ess_soc"),
        ("sensor.sigen_plant_ess_power", "plant_ess_power"),
        ("sensor.sigen_plant_grid_active_power", "plant_grid_sensor_active_power"),
        ("sensor.sigen_plant_pv_power", "plant_photovoltaic_power"),
        ("sensor.sigen_plant_accumulated_pv_energy", "plant_accumulated_pv_energy"),
        ("switch.sigen_plant_remote_ems", "plant_remote_ems_enable"),
        ("select.sigen_plant_remote_ems_control_mode", "plant_remote_ems_control_mode"),
        ("number.sigen_plant_grid_export_limit", "plant_grid_point_maximum_export_limitation"),
    ])
    m = presets.match(presets.PRESETS["sigenergy"], reg)
    assert m.roles["soc"] == "sensor.sigen_plant_battery_state_of_charge"
    assert m.roles["grid_power"] == "sensor.sigen_plant_grid_active_power"
    assert m.actions["charge"][1] == {"type": "select", "entity_id": "select.sigen_plant_remote_ems_control_mode",
                                      "value": "Command Charging (Grid First)"}
    assert m.export == ("number.sigen_plant_grid_export_limit", 0.0)
    assert "house_energy" in m.missing


def test_preset_deye_picks_exact_entity_and_optional_programs():
    reg = _reg("01JDEYE", [
        ("sensor.inverter_battery", "battery_sensor"),
        ("sensor.inverter_total_battery", "total_battery_sensor"),     # må ikke forveksles med "battery_sensor"
        ("sensor.inverter_battery_power", "battery_power_sensor"),
        ("select.inverter_work_mode", "work_mode_select"),
        ("select.inverter_program_1_charging", "program_1_charging_select"),
        ("number.inverter_program_1_soc", "program_1_soc_number"),
        ("select.inverter_program_2_charging", "program_2_charging_select"),
        ("number.inverter_program_2_soc", "program_2_soc_number"),
    ])
    m = presets.match(presets.PRESETS["deye"], reg)
    assert m.roles["soc"] == "sensor.inverter_battery"
    ch = m.actions["charge"]
    assert len(ch) == 4 and ch[0]["value"] == "Grid" and ch[1]["value"] == "max_soc"   # program 3–6 findes ikke: springes over
    normal = presets.fill_keep(m.actions, {"select.inverter_work_mode": "Zero Export To Load"},
                               presets.keep_fallbacks(m.actions, reg))
    assert normal["normal"][0] == {"type": "select", "entity_id": "select.inverter_work_mode", "value": "Zero Export To Load"}
    # Står den i SmartHus' egen tilstand, bruges standarden i stedet
    bad = presets.fill_keep(m.actions, {"select.inverter_work_mode": "Export First"}, presets.keep_fallbacks(m.actions, reg))
    assert bad["normal"][0]["value"] == "Zero Export To CT"


def test_preset_growatt_uses_first_variant_that_fits():
    reg = _reg("Growatt", [
        ("select.growatt_priority", "priority"),
        ("select.growatt_battery_first_charge_from_grid", "battery_first_charge_from_grid"),
        ("sensor.growatt_battery_soc", "battery_soc"),
    ])
    m = presets.match(presets.PRESETS["growatt"], reg)
    assert [s["entity_id"] for s in m.actions["charge"]] == ["select.growatt_priority", "select.growatt_battery_first_charge_from_grid"]
    # Uden styrings-entiteter: ingen styring, og det fortælles, hvad der mangler
    m2 = presets.match(presets.PRESETS["growatt"], reg[2:])
    assert m2.actions is None and m2.control_missing


def test_preset_huawei_uses_battery_device_and_skips_disabled():
    reg = [presets.Entry("sensor.batteries_state_of_capacity", "HV123_storage_state_of_capacity", "bat-dev"),
           presets.Entry("sensor.inverter_input_power", "HV123_input_power", "inv-dev", disabled=True)]
    m = presets.match(presets.PRESETS["huawei"], reg)
    assert m.huawei_device == "bat-dev" and m.actions is None
    assert "sensor.inverter_input_power" in m.disabled and "solar_power" not in m.roles


def test_preset_growatt_cloud_sph_and_min():
    """Growatt Server (sky): inverteren findes via SOC-sensoren, typen af nøglerne."""
    reg = [presets.Entry("sensor.omc_state_of_charge", "OMC123-mix_statement_of_charge", "inv-dev"),
           presets.Entry("sensor.omc_lifetime_solar_energy", "OMC123-mix_solar_generation_lifetime", "inv-dev"),
           presets.Entry("sensor.omc_ac_frequency", "OMC123-sph_grid_frequency", "inv-dev", disabled=True),
           presets.Entry("sensor.plant_total_energy_today", "1485033-total_energy_today", None)]
    m = presets.match(presets.PRESETS["growatt_cloud"], reg)
    assert (m.growatt_device, m.growatt_type) == ("inv-dev", "sph") and m.huawei_device is None
    assert m.roles["soc"] == "sensor.omc_state_of_charge" and m.roles["solar_energy"] == "sensor.omc_lifetime_solar_energy"
    # Effekt: køb/salg og lad/aflad som hver sin sensor
    reg_p = reg + [presets.Entry("sensor.omc_import_from_grid", "OMC123-mix_import_from_grid", "inv-dev"),
                   presets.Entry("sensor.omc_import_from_grid_today", "OMC123-mix_import_from_grid_today", "inv-dev"),
                   presets.Entry("sensor.omc_export_to_grid", "OMC123-mix_export_to_grid", "inv-dev"),
                   presets.Entry("sensor.omc_battery_charging", "OMC123-mix_battery_charge", "inv-dev"),
                   presets.Entry("sensor.omc_battery_charged_today", "OMC123-mix_battery_charge_today", "inv-dev"),
                   presets.Entry("sensor.omc_battery_discharging_w", "OMC123-mix_battery_discharge_w", "inv-dev")]
    mp = presets.match(presets.PRESETS["growatt_cloud"], reg_p)
    assert (mp.roles["grid_import_power"], mp.roles["grid_export_power"]) == ("sensor.omc_import_from_grid", "sensor.omc_export_to_grid")
    assert (mp.roles["battery_charge_power"], mp.roles["battery_discharge_power"]) == \
        ("sensor.omc_battery_charging", "sensor.omc_battery_discharging_w")
    assert mp.roles["import_energy"] == "sensor.omc_import_from_grid_today"
    # MIN TL-XH: alternative nøgler ("a|b") findes
    reg2 = [presets.Entry("sensor.min_soc", "MIN9-tlx_statement_of_charge", "min-dev"),
            presets.Entry("sensor.min_house", "MIN9-mix_load_consumption_total", "min-dev")]
    m2 = presets.match(presets.PRESETS["growatt_cloud"], reg2)
    assert (m2.growatt_device, m2.growatt_type) == ("min-dev", "min") and m2.roles["house_energy"] == "sensor.min_house"
    # Uden SOC: ingen styring
    assert presets.match(presets.PRESETS["growatt_cloud"], reg[1:]).growatt_device is None


def test_growatt_block_window_and_commands():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    gw = sys.modules["smarthus.growatt"]
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    def slots(start_local, n):
        t0 = start_local.astimezone(timezone.utc)
        return [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 1.0, 1.0, 0.5) for i in range(n)]
    # Ladeblok 02:00–05:00 (vintertid) – midt i blokken: hele blokken sendes
    ch = slots(datetime(2026, 11, 2, 2, 0, tzinfo=tz), 12)
    assert gw.block_window(ch, ch[5], tz) == ("02:00", "05:00")
    # To adskilte blokke: kun den, vi står i
    other = slots(datetime(2026, 11, 2, 13, 0, tzinfo=tz), 4)
    assert gw.block_window(ch + other, other[1], tz) == ("13:00", "14:00")
    # Hen over midnat: klippes ved 23:59 / starter 00:00 næste døgn
    night = slots(datetime(2026, 11, 2, 23, 0, tzinfo=tz), 8)
    assert gw.block_window(night, night[0], tz) == ("23:00", "23:59")
    assert gw.block_window(night, night[5], tz) == ("00:00", "01:00")
    # Ikke i blokken (lav salgspris): kun det aktuelle kvarter
    lone = slots(datetime(2026, 11, 2, 12, 15, tzinfo=tz), 1)[0]
    assert gw.block_window(ch, lone, tz) == ("12:15", "12:30")

    assert gw.device_type("X-mix_statement_of_charge") == "sph" and gw.device_type("X-tlx_bms_soc") == "min"
    assert gw.device_type("1485033-total_energy_today") is None
    c = gw.commands("sph", "dev", "charge", ("02:00", "05:00"), 95, 15, 80)
    assert [x[0] for x in c] == ["write_ac_discharge_times", "write_ac_charge_times"]     # aflad slås fra først
    assert c[0][1] == {"device_id": "dev", "period_1_enabled": False, "period_2_enabled": False, "period_3_enabled": False}
    assert c[1][1] == {"device_id": "dev", "charge_power": 80, "charge_stop_soc": 95, "mains_enabled": True,
                       "period_1_start": "02:00", "period_1_end": "05:00", "period_1_enabled": True,
                       "period_2_enabled": False, "period_3_enabled": False}
    d = gw.commands("sph", "dev", "discharge", ("17:00", "19:00"), 95, 15, 100)
    assert [x[0] for x in d] == ["write_ac_charge_times", "write_ac_discharge_times"]
    assert d[0][1]["mains_enabled"] is False and d[1][1]["discharge_stop_soc"] == 15
    n = gw.commands("sph", "dev", "normal", None, 95, 15, 100)
    assert [x[0] for x in n] == ["write_ac_charge_times", "write_ac_discharge_times"]
    assert not any(v for k, v in n[0][1].items() if k.endswith("_enabled"))
    mn = gw.commands("min", "dev", "discharge", ("17:00", "19:00"), 95, 15, 100)
    assert mn == [("update_time_segment", {"device_id": "dev", "segment_id": 1, "batt_mode": "grid_first",
                                           "start_time": "17:00", "end_time": "19:00", "enabled": True})]
    assert gw.commands("min", "dev", "normal", None, 95, 15, 100)[0][1]["enabled"] is False


def test_preset_fronius_dash_ids_and_storage_control():
    """Fronius (indbygget): unique_id med "-", lagerstyring via Modbus-grænser."""
    reg = [presets.Entry("sensor.byd_soc", "BYD123-state_of_charge"),
           presets.Entry("sensor.solarnet_power_grid", "solarnet-power_flow-power_grid", disabled=True),
           presets.Entry("sensor.solarnet_power_grid_import", "solarnet-power_flow-power_grid_import"),
           presets.Entry("sensor.meter_energy_consumed", "MTR1-energy_real_consumed")]
    for key in ("battery_charge_power_limit", "battery_discharge_power_limit"):
        reg.append(presets.Entry(f"number.gen24_{key}", f"INV1-modbus-{key}"))
    for key in ("battery_charge_power_limit_enabled", "battery_discharge_power_limit_enabled", "battery_grid_charging"):
        reg.append(presets.Entry(f"switch.gen24_{key}", f"INV1-modbus-{key}"))
    m = presets.match(presets.PRESETS["fronius"], reg)
    assert m.roles["soc"] == "sensor.byd_soc" and m.roles["import_energy"] == "sensor.meter_energy_consumed"
    assert m.roles["grid_import_power"] == "sensor.solarnet_power_grid_import"   # "power_grid" (slået fra) bruges ikke
    assert "sensor.solarnet_power_grid" not in m.disabled and "grid_power" not in m.missing
    ch = m.actions["charge"]
    assert ch[3] == {"type": "number", "entity_id": "number.gen24_battery_discharge_power_limit", "value": -100}


def test_preset_fronius_as_seen_on_a_gen24():
    """Rigtige unique_id'er: strengenes energi frem for inverterens, batteriets målere og husets effekt."""
    S = "solar_net_33121087-power_flow-"
    reg = [presets.Entry("sensor.total", "33121087-energy_total"), presets.Entry("sensor.net_total", S + "energy_total"),
           presets.Entry("sensor.pv", "33121087-modbus-energy_total_pv"),
           presets.Entry("sensor.bat_in", "33121087-modbus-storage_energy_charged_total"),
           presets.Entry("sensor.bat_out", "33121087-modbus-storage_energy_discharged_total"),
           presets.Entry("sensor.chg", S + "power_battery_charge"), presets.Entry("sensor.dis", S + "power_battery_discharge"),
           presets.Entry("sensor.load", S + "power_load_consumed"), presets.Entry("sensor.loadg", S + "power_load_generated"),
           presets.Entry("sensor.cap", "P030T020Z2205160572     -maximum_capacity")]
    m = presets.match(presets.PRESETS["fronius"], reg)
    assert m.roles["solar_energy"] == "sensor.pv" and m.roles["battery_charge_energy"] == "sensor.bat_in"
    assert m.roles["battery_charge_power"] == "sensor.chg" and m.house_power == "sensor.load" and m.capacity == "sensor.cap"
    # Uden Modbus findes strengenes måler ikke: så bruges inverterens
    m = presets.match(presets.PRESETS["fronius"], [e for e in reg if e.entity_id != "sensor.pv"])
    assert m.roles["solar_energy"] == "sensor.total"


def test_battery_charge_split_between_grid_and_solar():
    lay = {"import": ["i"], "export": ["e"], "solar": ["s"], "battery_in": ["bi"], "battery_out": ["bo"], "devices": []}
    # Tvunget fra nettet om natten: alt fra nettet
    f = energy.hour_fields(lay, {"i": 5, "e": 0, "s": 0, "bi": 3.8, "bo": 0}, 2.0, 1.0)
    assert f["bat_in_grid"] == 3.8 and f["bat_in_solar"] == 0
    # Solen dækker huset (1 kWh) og lader med resten (2 kWh); intet fra nettet
    f = energy.hour_fields(lay, {"i": 0, "e": 0, "s": 3, "bi": 2, "bo": 0}, 2.0, 1.0)
    assert f["bat_in_solar"] == 2 and f["bat_in_grid"] == 0
    # Blandet: sol 1, hus 0,5, batteri 3 → 0,5 fra sol og 2,5 fra nettet
    f = energy.hour_fields(lay, {"i": 2.5, "e": 0, "s": 1, "bi": 3, "bo": 0}, 2.0, 1.0)
    assert round(f["bat_in_solar"], 3) == 0.5 and round(f["bat_in_grid"], 3) == 2.5 and round(f["bat_in_grid_kr"], 3) == 5.0
    # Sol til batteriet til salgspris (1 kr), resten til købspris (2 kr) – regnskabet går stadig op
    assert round(f["bat_in_solar_kr"], 3) == 0.5 and round(f["bat_in_kr"], 3) == 5.5
    assert round(f["solar_self"], 3) == 0.5 and round(f["solar_self_kr"], 3) == 1.0 and round(f["solar_kr"], 3) == 1.5
    assert round(f["bill"] + f["solar_kr"] + f["bat_out_kr"] - f["bat_in_kr"], 3) == round(f["house_kr"], 3)


tariffs = _load("tariffs")


def test_tariffs_from_datahub_rows():
    """Nettarif pr. time og periode, Energinet + elafgift – og de bruges i prisen (også bagud i tid)."""
    grid = [{"ChargeTypeCode": "DT_C_01", "ValidFrom": "2026-10-01T00:00:00", "ValidTo": "2027-04-01T00:00:00",
             **{f"Price{i}": (0.955573 if 18 <= i <= 21 else 0.318524 if i >= 7 else 0.106175) for i in range(1, 25)}},
            {"ChargeTypeCode": "DT_C_01", "ValidFrom": "2026-04-01T00:00:00", "ValidTo": "2026-10-01T00:00:00",
             "Price1": 0.106175, **{f"Price{i}": 0.414082 for i in range(18, 22)}}]
    tso = [{"ChargeTypeCode": "40000", "ValidFrom": "2026-01-01T00:00:00", "ValidTo": "2027-01-01T00:00:00", "Price1": 0.043},
           {"ChargeTypeCode": "41000", "ValidFrom": "2026-01-01T00:00:00", "ValidTo": "2027-01-01T00:00:00", "Price1": 0.072},
           {"ChargeTypeCode": "EA-001", "ValidFrom": "2026-01-01T00:00:00", "ValidTo": "2028-01-01T00:00:00", "Price1": 0.008},
           {"ChargeTypeCode": "EA-001", "ValidFrom": "2025-01-01T00:00:00", "ValidTo": "2026-01-01T00:00:00", "Price1": 0.72}]
    t = tariffs.Tariffs(TZ, tariffs.parse_rows(grid)["DT_C_01"], tariffs.parse_rows(tso))
    peak = datetime(2026, 10, 28, 17, 30, tzinfo=TZ)             # kl. 17–18 = Price18
    assert t.net(peak) == 0.955573 and t.net(datetime(2026, 10, 28, 3, tzinfo=TZ)) == 0.106175
    assert t.net(datetime(2026, 7, 1, 10, tzinfo=TZ)) == 0.106175   # sommer: kun Price1 + spidslast → Price1 udfylder
    assert abs(t.transmission(peak) - 0.115) < 1e-9 and t.elafgift(peak) == 0.008
    assert t.elafgift(datetime(2025, 6, 1, 12, tzinfo=TZ)) == 0.72 and t.transmission(datetime(2025, 6, 1, tzinfo=TZ)) is None
    m = pricing.PriceModel(dict(const.DEFAULT_PRICES), TZ)
    m.tariffs = t
    expected = round((1.0 + 0.05 + 0.955573 + 0.115 + 0.008) * 1.25, 4)
    assert m.buy_price(1.0, peak) == expected


def test_savings_replace_today_after_unclean_restart():
    """Gemte tal fra i går (Home Assistant lukkede ikke pænt ned): dagens køb genopbygges fra statistikken,
    og måned/år/total rettes med forskellen – uden dobbelttælling."""
    sv = savings.SavingsCalculator()
    yday = datetime(2026, 10, 28, 23, 50, tzinfo=TZ)
    sv.update(yday, None, None, {"import": 100.0})          # målerens startværdi
    sv.data["solar"]["month"]["import_kwh"] = 200.0
    sv.data["solar"]["total"]["import_kwh"] = 900.0
    now = datetime(2026, 10, 29, 4, 5, tzinfo=TZ)
    rows = [{"start": datetime(2026, 10, 29, h, tzinfo=TZ), "import": 1.0, "price": 2.0, "sale": 0.5} for h in range(4)]
    rows.append({"start": datetime(2026, 10, 28, 23, tzinfo=TZ), "import": 9.0, "price": 2.0})   # i går: ignoreres
    res = sv.replace_today(rows, now, {"import": 104.0})
    assert res["import_kwh"] == 4.0
    assert sv.data["solar"]["today"]["import_kwh"] == 4.0 and sv.data["solar"]["today"]["import_cost"] == 8.0
    assert sv.data["solar"]["month"]["import_kwh"] == 204.0 and sv.data["solar"]["total"]["import_kwh"] == 904.0
    assert sv.data["last"]["import"] == 104.0
    # Den løbende optælling fortsætter fra målerens værdi
    sv.update(datetime(2026, 10, 29, 4, 10, tzinfo=TZ), None, None, {"import": 104.5})
    # Kører den igen (fx næste opstart), tælles intet dobbelt
    sv.replace_today(rows, now, {"import": 104.0})
    assert sv.data["solar"]["month"]["import_kwh"] == 204.0


def _runs(chosen):
    out = []
    for x in chosen:
        if out and out[-1][-1].end == x.start:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def test_battery_blocks_no_on_off_at_night():
    """Kvarterpriser, der svinger med få øre om natten, må ikke give lad-stop-lad (som 01:45–02:00, 02:15–03:30 …)."""
    from datetime import datetime, timedelta, timezone
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    night = [2.66, 2.64, 2.66, 2.65, 2.62, 2.61, 2.60, 2.58, 2.55, 2.57, 2.54, 2.53, 2.52, 2.55, 2.53, 2.54,
             2.51, 2.52, 2.55, 2.50, 2.52, 2.51, 2.53, 2.56] + [3.1] * 8
    slots = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, p, p, 1.5) for i, p in enumerate(night)]
    old = planner.cheapest(slots, 3)
    assert len(_runs(old)) > 2                        # den gamle måde hakker
    new = planner.cheapest_blocks(slots, 3, 1.0, min_gap_h=1.0)
    assert len(new) == 12 and len(_runs(new)) == 1    # én sammenhængende blok på 3 timer
    assert sum(s.price for s in new) - sum(s.price for s in old) < 0.6   # koster kun få øre i alt
    # Aflad i de dyreste – samme regel
    dis = planner.cheapest_blocks(slots, 1, 1.0, min_gap_h=1.0, most_expensive=True)
    assert len(_runs(dis)) == 1 and all(s.price == 3.1 for s in dis)


def test_cheapest_blocks_is_optimal_with_block_and_gap_rules():
    """Sammenlign med en udtømmende søgning på små tilfældige dage."""
    import itertools, random
    from datetime import datetime, timedelta, timezone
    rnd = random.Random(7)
    q = timedelta(minutes=15)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def ok(idx, m, mg, total):
        sel = set(idx)
        runs, i = [], 0
        while i < total:
            if i in sel:
                j = i
                while j + 1 < total and j + 1 in sel:
                    j += 1
                runs.append((i, j)); i = j + 1
            else:
                i += 1
        if any(b - a + 1 < m for a, b in runs):
            return False
        return all(runs[k + 1][0] - runs[k][1] - 1 >= mg for k in range(len(runs) - 1))
    for trial in range(60):
        total = rnd.randint(8, 13)
        prices = [round(rnd.uniform(1, 3), 2) for _ in range(total)]
        slots = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, p, p, 0) for i, p in enumerate(prices)]
        n = rnd.randint(2, 6)
        for exp in (False, True):
            got = planner.cheapest_blocks(slots, n * 0.25, 0.5, min_gap_h=0.5, most_expensive=exp)
            sign = -1 if exp else 1
            best = min((sum(sign * prices[i] for i in c) for c in itertools.combinations(range(total), n)
                        if ok(c, 2, 2, total)), default=None)
            if best is None:
                continue        # ingen plan opfylder reglerne: der falder den tilbage
            idx = [slots.index(s) for s in got]
            assert ok(idx, 2, 2, total) and abs(sum(sign * prices[i] for i in idx) - best) < 1e-9, (prices, n, exp)


def test_preset_sigenergy_hold_needs_discharge_limit_and_restores_it():
    base = [("sensor.sigen_soc", "plant_ess_soc"), ("switch.sigen_ems", "plant_remote_ems_enable"),
            ("select.sigen_mode", "plant_remote_ems_control_mode")]
    m = presets.match(presets.PRESETS["sigenergy"], _reg("01J_plant", base))
    assert m.actions and "hold" not in m.actions                         # uden afladegrænsen: som før
    reg = _reg("01J_plant", base + [("number.sigen_dis", "plant_ess_max_discharging_limit"),
                                    ("sensor.sigen_rated_dis", "plant_ess_rated_discharging_power")])
    m = presets.match(presets.PRESETS["sigenergy"], reg)
    assert {"type": "number", "entity_id": "number.sigen_dis", "value": 0} in m.actions["hold"]
    # Normal sætter grænsen tilbage til den nuværende værdi …
    a = presets.fill_keep(m.actions, {"number.sigen_dis": "8.0", "select.sigen_mode": "Maximum Self Consumption"}, {})
    assert {"type": "number", "entity_id": "number.sigen_dis", "value": "8.0"} in a["normal"]
    assert all(any(x["entity_id"] == "number.sigen_dis" for x in a[st]) for st in ("charge", "discharge", "normal"))
    # … men står den på 0 (SmartHus' egen gem-værdi), bruges den nominelle effekt
    assert presets.keep_sensor_fallbacks(m.actions, reg) == {"number.sigen_dis": "sensor.sigen_rated_dis"}
    a = presets.fill_keep(m.actions, {"number.sigen_dis": "0.0"}, {"number.sigen_dis": "10.0"})
    assert {"type": "number", "entity_id": "number.sigen_dis", "value": "10.0"} in a["normal"]
    # Kan den ikke sættes tilbage, bruges "gem" slet ikke
    a = presets.fill_keep(m.actions, {"number.sigen_dis": "0.0"}, {})
    assert "hold" not in a and a["normal"]


def test_preset_hold_for_deye_solax_foxess_fronius():
    """"Gem batteriet" = maks. afladning 0, og de andre tilstande sætter den tilbage. Mangler tallet: som før."""
    cases = [
        ("deye", [("sensor.d_soc", "battery_sensor"), ("select.d_mode", "work_mode_select"),
                  ("select.d_p1", "program_1_charging_select"), ("number.d_p1soc", "program_1_soc_number")],
         ("number.d_dis", "battery_max_discharging_current_number")),
        ("solax", [("sensor.s_soc", "battery_capacity"), ("select.s_use", "charger_use_mode"),
                   ("select.s_man", "manual_mode_select")],
         ("number.s_dis", "battery_discharge_max_current")),
        ("foxess", [("sensor.f_soc", "battery_soc"), ("select.f_mode", "work_mode")],
         ("number.f_dis", "max_discharge_current")),
    ]
    for key, base, num in cases:
        m = presets.match(presets.PRESETS[key], _reg("01J" + key, base))
        assert m.actions and "hold" not in m.actions, key
        m = presets.match(presets.PRESETS[key], _reg("01J" + key, base + [num]))
        assert {"type": "number", "entity_id": num[0], "value": 0} in m.actions["hold"], key
        a = presets.fill_keep(m.actions, {num[0]: "25", base[1][0]: "Self Use"}, presets.keep_fallbacks(m.actions, []))
        for st in ("charge", "discharge", "normal"):
            assert {"type": "number", "entity_id": num[0], "value": "25"} in a[st], (key, st)
        assert a["hold"][-1] == {"type": "number", "entity_id": num[0], "value": 0}, key
        a = presets.fill_keep(m.actions, {num[0]: "0"}, {})
        assert "hold" not in a, key                       # kan ikke sættes tilbage: "gem" bruges ikke
    # Fronius: afladegrænse 0 % slået til, ingen netopladning
    m = presets.match(presets.PRESETS["fronius"], _reg("01JFRO", [
        ("sensor.fr_soc", "state_of_charge"),
        ("number.fr_chg", "battery_charge_power_limit"), ("switch.fr_chg_on", "battery_charge_power_limit_enabled"),
        ("number.fr_dis", "battery_discharge_power_limit"), ("switch.fr_dis_on", "battery_discharge_power_limit_enabled"),
        ("switch.fr_grid", "battery_grid_charging")]))
    h = m.actions["hold"]
    assert {"type": "number", "entity_id": "number.fr_dis", "value": 0} in h
    assert {"type": "switch", "entity_id": "switch.fr_dis_on", "value": True} in h
    assert {"type": "switch", "entity_id": "switch.fr_grid", "value": False} in h
    assert "hold" in presets.fill_keep(m.actions, {}, {})


def test_plan_hold_saves_battery_for_expensive_hours():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen")
    prof = profile.Profile(weekday=[0.5] * 24, weekend=[0.5] * 24, days=5)
    t0 = datetime(2026, 11, 2, 6, 0, tzinfo=tz)
    q = timedelta(minutes=15)
    # 06–16 billigt (2,7), 16–20 dyrt (4,6), 20–24 middel (3,0)
    prices = [2.7] * 40 + [4.6] * 16 + [3.0] * 16
    slots = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, p, p, 1.0) for i, p in enumerate(prices)]
    until = t0 + timedelta(hours=18)
    # 2 kWh rækker kun til de dyre timer (4 t × 0,5 kWh / 95 %) – resten af tiden gemmes batteriet
    hold, limit = profile.plan_hold(slots, prof, t0, until, 2.0)
    assert limit == 4.6 and {s.price for s in hold} == {2.7} and len(hold) == 40   # efter spidsen er batteriet tomt
    # Lidt mere strøm (2,2 kWh) rækker også til starten af aftenen – så gemmes kun de billigste timer
    hold, limit = profile.plan_hold(slots, prof, t0, until, 2.2)
    assert limit == 3.0 and {s.price for s in hold} == {2.7}
    # Nok strøm til hele perioden: intet gemmes
    assert profile.plan_hold(slots, prof, t0, until, 20.0) == ([], None)
    # Tomt batteri: intet at gemme
    assert profile.plan_hold(slots, prof, t0, until, 0.0) == ([], None)
    # Kun lidt billigere end grænsen (under 0,10 kr): gemmes ikke
    flat = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 3.0 + (0.05 if i >= 40 else 0), 0, 0) for i in range(72)]
    assert profile.plan_hold(flat, prof, t0, until, 2.0)[0] == []
    # Ingen enkelte kvarterer: et billigt kvarter alene gemmes ikke
    one = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, 2.0 if i == 10 else 4.0, 0, 0) for i in range(72)]
    assert profile.plan_hold(one, prof, t0, until, 2.0)[0] == []


def test_battery_sells_only_when_sale_price_beats_charge_cost():
    """Salgsprisen (ikke købsprisen) afgør salg: lad til huset, men sælg ikke med tab."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    def day(sale_peak):
        out = []
        for i in range(96):
            h = i // 4
            buy = 2.4 if 1 <= h < 5 else 3.9 if 17 <= h < 20 else 2.8
            sale = sale_peak if 17 <= h < 20 else 1.7
            out.append(planner.Slot(t0 + i * q, t0 + (i + 1) * q, buy - 1.0, buy, sale))
        return out
    st = dict(const.DEFAULT_SETTINGS, battery_charge_hours=3, battery_discharge_hours=2, battery_min_spread=0.5)
    p = planner.build_plan(day(2.3), t0, tz, st)
    assert len(p.battery_charge) == 12 and p.battery_discharge == []      # lad til huset, sælg ikke (2,3 < 2,67 + 0,5)
    p = planner.build_plan(day(3.5), t0, tz, st)
    assert len(p.battery_charge) == 12 and len(p.battery_discharge) == 8  # 3,5 kr i salg: sælg 2 timer
    assert all(s.sale == 3.5 for s in p.battery_discharge)


def test_forecast_battery_charge_use_hold_and_sell():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 0, 0, tzinfo=tz)
    prof = profile.Profile(weekday=[0.4] * 24, weekend=[0.4] * 24, days=5)
    def slots(sale_peak=1.7):
        out = []
        for i in range(96):
            h = i // 4
            buy = 2.4 if 1 <= h < 4 else 4.0 if 17 <= h < 20 else 2.8
            out.append(planner.Slot(t0 + i * q, t0 + (i + 1) * q, buy, buy, sale_peak if 17 <= h < 20 else 1.7))
        return out
    sl = slots()
    ch = [s for s in sl if 1 <= s.start.hour < 4]
    f = profile.forecast_battery(sl, prof, t0, battery_kwh=1.0, floor_kwh=1.0, full_kwh=6.0, charge=ch, hold=[],
                                 sell=[], stock_price=2.4, can_hold=True)
    d = f["days"]["2026-11-02"]
    assert d["charge_kwh"] == 5.0 and d["charge_cost"] == 12.0
    assert abs(d["house_kwh"] - 5.0) < 0.01 and d["house_saving"] > 0
    used = {s.start.hour for s in f["use"]}
    assert {17, 18, 19} <= used                      # aftenspidsen dækkes, fordi batteriet gemmes i billige timer
    assert d["sell_kwh"] == 0
    # Uden "gem": batteriet bruges fra morgenstunden og er tomt før aftenspidsen
    g = profile.forecast_battery(sl, prof, t0, battery_kwh=1.0, floor_kwh=1.0, full_kwh=6.0, charge=ch, hold=[],
                                 sell=[], stock_price=2.4, can_hold=False)
    assert not ({17, 18, 19} & {s.start.hour for s in g["use"]})
    assert g["days"]["2026-11-02"]["house_saving"] < d["house_saving"]
    # Høj salgspris: der sælges kun det, der er ud over husets behov til næste opladning
    sp = slots(6.0)
    sell = [s for s in sp if s.start.hour == 17]
    h = profile.forecast_battery(sp, prof, t0 + timedelta(hours=16), battery_kwh=6.0, floor_kwh=1.0, full_kwh=6.0, charge=[], hold=[],
                                 sell=sell, stock_price=2.4, can_hold=False)
    e = h["days"]["2026-11-02"]
    assert e["sell_kwh"] > 0 and e["sell_gain"] > 0 and e["sell_kwh"] + e["house_kwh"] <= 5.0 + 1e-6
    assert h["sell"] and {x.start.hour for x in h["sell"]} == {17}
    # Kun reserven i batteriet: salgstimen er planlagt, men der forventes intet salg
    k = profile.forecast_battery(sp, prof, t0 + timedelta(hours=16), battery_kwh=2.0, floor_kwh=1.0, full_kwh=6.0, charge=[], hold=[],
                                 sell=sell, stock_price=2.4, can_hold=False)
    assert k["sell"] == [] and k["days"]["2026-11-02"]["sell_kwh"] == 0


def test_profile_ignores_days_without_data():
    """Døgn før måleren fandtes (0 kWh hele døgnet) må ikke trække forbruget ned."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen")
    hours = []
    d0 = datetime(2026, 10, 19, 0, 0, tzinfo=tz)       # mandag
    for day in range(14):
        for h in range(24):
            t = d0 + timedelta(days=day, hours=h)
            hours.append((t, 0.0 if day < 7 else 0.4))   # første uge: ingen data
    # Måleren startede kl. 12 på den første dag med data (opsætningsdagen) – timerne før tæller ikke
    hours = [(t, (0.0 if t < d0 + timedelta(days=7, hours=12) else k)) for t, k in hours]
    p = profile.build_profile(hours)
    assert abs(sum(p.weekday) - 9.6) < 0.01 and abs(sum(p.weekend) - 9.6) < 0.01
    assert (p.weekday_days, p.weekend_days) == (5, 2)


def test_plan_hold_counts_on_solar_refill():
    """Fylder solen batteriet midt på dagen, må det bruges i morgenspidsen – det skal ikke gemmes hele dagen."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen")
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 7, 0, tzinfo=tz)
    prof = profile.Profile(weekday=[0.5] * 24, weekend=[0.5] * 24, days=5)
    # 07–09 dyrt (3,5), 09–16 billigt (2,6), 16–21 dyrest (4,5)
    prices = [3.5] * 8 + [2.6] * 28 + [4.5] * 20
    slots = [planner.Slot(t0 + i * q, t0 + (i + 1) * q, p, p, 1.6) for i, p in enumerate(prices)]
    until = t0 + timedelta(hours=14)
    no_sun, _ = profile.plan_hold(slots, prof, t0, until, 2.0)
    assert any(s.price == 3.5 for s in no_sun)                 # uden sol: morgenspidsen gemmes til aftenen
    sun = {t0.replace(hour=h): 2.0 for h in range(10, 15)}     # 5 timer à 2 kWh – rigeligt overskud
    with_sun, _ = profile.plan_hold(slots, prof, t0, until, 2.0, solar=sun, room_kwh=4.0)
    assert not any(s.price == 3.5 for s in with_sun)           # med sol: brug batteriet nu, solen fylder op
    f = profile.forecast_battery(slots, prof, t0, battery_kwh=3.0, floor_kwh=1.0, full_kwh=5.0, charge=[],
                                 hold=with_sun, sell=[], stock_price=2.4, can_hold=True, solar=sun)
    d = f["days"]["2026-11-02"]
    assert d["solar_kwh"] >= 3.0 and {7, 16, 17, 18, 19, 20} <= {s.start.hour for s in f["use"]}
    # Når batteriet er fuldt, dækker solen huset alene (vises som lys sol i grafen)
    assert f["sun_cover"] and not set(f["sun_cover"]) & set(f["solar"]) and all(10 <= s.start.hour < 15 for s in f["sun_cover"])


def test_sun_tracker_scales_rest_of_day():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    W = _load("weather")
    tz = ZoneInfo("Europe/Copenhagen")
    t = W.SunTracker()
    now = datetime(2026, 11, 2, 8, 0, tzinfo=tz)
    for i in range(121):                      # 08:00–10:00 hvert minut: prognose 1 kW, faktisk 0,15 kW
        t.update(now + timedelta(minutes=i), 0.15, 1.0)
    assert abs(t.forecast - 2.0) < 0.02 and abs(t.ratio - 0.15) < 0.01
    assert abs(t.factor(0.7) - 0.15) < 0.01   # solen svigter: regn kun med 15 %
    t2 = W.SunTracker()
    for i in range(121):
        t2.update(now + timedelta(minutes=i), 1.2, 1.0)
    assert t2.factor(0.7) == 0.7              # mere sol end ventet: stadig forsigtigt (70 %)
    t2.update(now + timedelta(days=1), 0.0, 0.0)
    assert t2.ratio is None and t2.factor(0.7) == 0.7    # nyt døgn: forfra
    # Lange huller (fx genstart) tæller højst 3 minutter
    t3 = W.SunTracker()
    t3.update(now, 1.0, 1.0); t3.update(now + timedelta(hours=2), 1.0, 1.0)
    assert abs(t3.forecast - 0.05) < 1e-9



def test_forecast_sensor_ids_open_meteo():
    weather = _load("weather")
    ex = {"sensor.hus_energy_production_today", "sensor.hus_energy_production_today_remaining",
          "sensor.hus_energy_production_tomorrow", "sensor.hus_power_production_now"}
    ids = weather.forecast_sensor_ids(None, ex)                                  # fundet automatisk
    assert ids == {"today": "sensor.hus_energy_production_today", "remaining": "sensor.hus_energy_production_today_remaining",
                   "tomorrow": "sensor.hus_energy_production_tomorrow", "power_now": "sensor.hus_power_production_now"}
    assert weather.forecast_sensor_ids("sensor.hus_energy_production_today_remaining", ex) == ids  # valgt
    fs = {"sensor.energy_production_today", "sensor.energy_production_today_remaining"}
    assert weather.forecast_sensor_ids(None, fs | ex)["remaining"] == "sensor.energy_production_today_remaining"
    assert weather.forecast_sensor_ids(None, set()) == {}


def test_battery_need_at_and_sell_switch():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Copenhagen")
    prof = profile.Profile(weekday=[0.5] * 24, weekend=[0.5] * 24, days=5)
    d = datetime(2026, 11, 4, 6, tzinfo=tz)
    end = d.replace(hour=0) + timedelta(days=1)
    # Ingen sol: 18 timer × 0,5 kWh oven på bunden (1 kWh)
    assert abs(profile.battery_need_at(prof, {}, d, end, floor=1, full=20) - 10.0) < 0.01
    # Sol midt på dagen fylder op – batteriet skal kun klare morgenen
    sun = {d.replace(hour=h): 3.0 for h in range(9, 15)}
    assert abs(profile.battery_need_at(prof, sun, d, end, floor=1, full=10) - 2.5) < 0.01
    # Selv fuldt rækker ikke: svaret er fuldt
    assert profile.battery_need_at(prof, {}, d, end, floor=1, full=5) == 5
    # Salg slået fra: batteriet sælger aldrig
    q = timedelta(minutes=15)
    t0 = datetime(2026, 11, 2, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    slots = []
    for i in range(96):
        h = i // 4
        buy = 2.4 if 1 <= h < 5 else 3.9 if 17 <= h < 20 else 2.8
        slots.append(planner.Slot(t0 + i * q, t0 + (i + 1) * q, buy - 1.0, buy, 3.5 if 17 <= h < 20 else 1.7))
    st = dict(const.DEFAULT_SETTINGS, battery_charge_hours=3, battery_discharge_hours=2, battery_min_spread=0.5)
    assert planner.build_plan(slots, t0, tz, st).battery_discharge
    assert planner.build_plan(slots, t0, tz, dict(st, battery_sell=False)).battery_discharge == []


def test_bil_uden_for_laderen_faar_pris_men_ingen_plan():
    now = datetime(2026, 11, 3, 19, 0, tzinfo=TZ)
    slots = []
    for i in range(4 * 24):
        t = now + timedelta(minutes=15 * i)
        slots.append(planner.Slot(t, t + timedelta(minutes=15), 0, 1.0 if t.hour < 6 else 3.0, 0.5))
    settings = dict(const.DEFAULT_SETTINGS)
    settings.update(ev_target_soc=80, ev_battery_capacity=60, ev_charge_power=11, ev_ready_hour=7, ev_max_total_power=11)
    cs = planner.CarInput
    out = planner.plan_cars(slots, now, TZ, settings, [cs("ev", "Bil", soc=40, connected=False, force=True), cs("ev2", "Bil 2", soc=40, connected=True)])
    a, b = out["ev"], out["ev2"]
    assert not a.connected and not a.on and a.slots == [] and not a.limited
    assert a.energy > 0 and a.cost_now > a.cost_planned > 0 and a.cost_horizon is not None
    # Den andens plan er urørt af bilen uden for laderen, selv med en fælles effektgrænse
    alone = planner.plan_cars(slots, now, TZ, settings, [cs("ev2", "Bil 2", soc=40, connected=True)])["ev2"]
    assert b.connected and [s.start for s in b.slots] == [s.start for s in alone.slots]
    assert b.cost_planned == alone.cost_planned


def _temp_house(k_true, days=28, noise=0.05, seed=1):
    """Syntetisk hus: dagsrytme + k × graddage, temperatur med døgnsving og vejrskift."""
    import math
    import random
    rnd = random.Random(seed)
    t0 = datetime(2026, 10, 14, tzinfo=TZ)
    hours, temps = [], {}
    for i in range(days * 24):
        t = t0 + timedelta(hours=i)
        temp = 9 + 5 * math.sin(i / 24 / 3.0) - 3 * math.cos(2 * math.pi * t.hour / 24) + rnd.uniform(-1, 1)
        base = 0.4 + (1.2 if 17 <= t.hour < 20 else 0.0)
        hours.append((t, base + k_true * max(16 - temp, 0) + rnd.uniform(-noise, noise)))
        temps[t] = temp
    return hours, temps


def test_temperature_model_learns_heating():
    hours, temps = _temp_house(0.08)
    m = profile.fit_temperature(hours, temps, profile_from=hours[-14 * 24][0])
    assert m.active and abs(m.k - 0.08) < 0.01, m.as_dict()
    # Koldere end de seneste 14 dage i morgen: mere forbrug
    tm = datetime(2026, 11, 11, 3, tzinfo=TZ)
    typ = m.typical_hdd[(False, 3)]
    adj = profile.temperature_adjust(m, {tm: 16 - typ - 5})        # 5 grader koldere end normalt kl. 3
    assert abs(adj[(tm.date(), 3)] - m.k * 5) < 1e-6
    p = profile.build_profile(hours[-14 * 24:])
    plain = p.hours_for(tm)[3]
    p.adjust = adj
    assert abs(p.hours_for(tm)[3] - (plain + m.k * 5)) < 1e-6
    # En meget mild dag trækker højst ned til 30 % af mønsteret
    p.adjust = {(tm.date(), 3): -100.0}
    assert abs(p.hours_for(tm)[3] - plain * profile.MIN_ADJUST_SHARE) < 1e-6


def test_temperature_model_off_without_electric_heating():
    hours, temps = _temp_house(0.0)
    m = profile.fit_temperature(hours, temps)
    assert not m.active and profile.temperature_adjust(m, {hours[0][0]: -10}) == {}


def test_temperature_model_needs_variation():
    hours, temps = _temp_house(0.08)
    flat = {t: 8.0 for t in temps}
    assert not profile.fit_temperature(hours, flat).active

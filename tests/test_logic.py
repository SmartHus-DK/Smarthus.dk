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

"""Test af læringen af solprognosen og bundgrænsen for husets forbrug (kører uden Home Assistant)."""

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PKG = Path(__file__).parents[1] / "custom_components" / "smarthus"
if "smarthus" not in sys.modules:
    pkg = types.ModuleType("smarthus")
    pkg.__path__ = [str(PKG)]
    sys.modules["smarthus"] = pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"smarthus.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"smarthus.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


suncal = _load("suncal")
profile = _load("profile")
TZ = ZoneInfo("Europe/Copenhagen")
NOW = datetime(2026, 11, 20, 0, 25, tzinfo=TZ)


def fill(days, fc_by_hour, actual_by_hour, skip=()):
    """Prognose og måling pr. UTC-time for et antal hele dage før NOW."""
    cal, actual = suncal.SolarCalibration(), {}
    base = NOW.astimezone(timezone.utc).replace(hour=0)
    for d in range(1, days + 1):
        if d in skip:
            continue
        day = base - timedelta(days=d)
        for h, f in fc_by_hour.items():
            key = suncal.hour_key(day.replace(hour=h))
            cal.data["forecast"][key] = f
            actual[key] = actual_by_hour.get(h, 0.0)
    return cal, actual


def at(hour):
    return datetime(2026, 11, 21, hour, tzinfo=timezone.utc)


def test_uaendret_prognose_indtil_der_er_laert_nok():
    cal, actual = fill(2, {10: 1.0}, {10: 2.0})
    cal.learn(actual, NOW)
    assert not cal.active
    assert cal.factor(at(10)) == 1.0


def test_laerer_skaev_prognose_time_for_time():
    cal, actual = fill(14, {7: 0.2, 10: 1.0, 15: 0.5}, {7: 0.0, 10: 2.0, 15: 0.05})
    cal.learn(actual, NOW)
    assert cal.active and cal.days == 14
    assert cal.factor(at(7)) < 0.4       # panelerne giver intet så tidligt
    assert cal.factor(at(10)) > 1.6      # midt på dagen rammer prognosen for lavt
    assert cal.factor(at(15)) < 0.4      # eftermiddagen ligger i skygge


def test_tillid_vokser_med_dagene():
    fc, act = {10: 1.0}, {10: 2.0}
    few, a1 = fill(3, fc, act)
    many, a2 = fill(20, fc, act)
    few.learn(a1, NOW)
    many.learn(a2, NOW)
    assert 1.0 < few.factor(at(10)) < many.factor(at(10)) <= 2.0


def test_udfald_og_nulstillet_maaler_taeller_ikke():
    cal, actual = fill(10, {10: 1.0}, {10: 1.0})
    good = cal.data["forecast"].copy()
    for key in list(good)[:3]:
        actual[key] = 0.0               # anlægget var nede
    actual[list(good)[3]] = 312.2       # måleren sprang
    cal.learn(actual, NOW)
    assert cal.days == 6
    assert cal.data["day_factor"] == 1.0


def test_egen_nedregulering_taeller_ikke():
    cal, actual = fill(10, {10: 1.0, 11: 1.0}, {10: 1.0, 11: 1.0})
    for key in cal.data["forecast"]:
        if key.endswith("T11"):
            actual[key] = 0.1
            cal.data["curtailed"].append(key)
    cal.learn(actual, NOW)
    assert cal.factor(at(11)) >= 0.99


def test_prognosen_fastfryses_ved_midnat():
    cal = suncal.SolarCalibration()
    noon = datetime(2026, 11, 20, 12, tzinfo=TZ)
    first = {noon + timedelta(hours=2): 1.0, noon + timedelta(days=1): 2.0}
    cal.record(first, noon)
    cal.record({h: v * 5 for h, v in first.items()}, noon + timedelta(minutes=15))
    fc = cal.data["forecast"]
    assert fc[suncal.hour_key(noon + timedelta(hours=2))] == 1.0     # i dag: står fast
    assert fc[suncal.hour_key(noon + timedelta(days=1))] == 10.0     # i morgen: må stadig ændre sig


def test_gemt_prognose_vinder_over_historik():
    cal = suncal.SolarCalibration({"forecast": {"2026-11-10T10": 1.0}})
    cal.backfill({"2026-11-10T10": 9.0, "2026-11-10T11": 2.0, "2026-11-10T12": 0.0})
    assert cal.data["forecast"] == {"2026-11-10T10": 1.0, "2026-11-10T11": 2.0}


def test_hver_prognosekilde_har_sin_egen_laering():
    cal, actual = fill(10, {10: 1.0}, {10: 2.0})
    cal.set_source("open_meteo_solar_forecast")
    cal.learn(actual, NOW)
    before = cal.factor(at(10))
    assert not cal.set_source("open_meteo_solar_forecast") and cal.active
    # Kort udfald: en anden prognose tager over og må ikke bruge eller slette det lærte
    assert cal.set_source("forecast_solar") and not cal.active and not cal.data["forecast"]
    assert cal.factor(at(10)) == 1.0
    cal.record({datetime(2026, 11, 21, 10, tzinfo=TZ): 3.0}, NOW)
    assert cal.set_source("open_meteo_solar_forecast") and cal.active
    assert cal.factor(at(10)) == before and cal.days == 10
    assert 3.0 not in cal.data["forecast"].values()
    assert cal.set_source("forecast_solar") and list(cal.data["forecast"].values()) == [3.0]


def test_vintertid_flytter_ikke_faktorerne():
    cal, actual = fill(10, {10: 1.0}, {10: 2.0})
    cal.learn(actual, NOW)
    summer = datetime(2027, 7, 1, 12, tzinfo=TZ)   # 10 UTC
    winter = datetime(2027, 1, 1, 11, tzinfo=TZ)   # 10 UTC
    assert cal.factor(summer) == cal.factor(winter) > 1.0


def test_data_kan_gemmes_og_hentes():
    import json
    cal, actual = fill(10, {10: 1.0}, {10: 2.0})
    cal.learn(actual, NOW)
    again = suncal.SolarCalibration(json.loads(json.dumps(cal.data)))
    assert again.factor(at(10)) == cal.factor(at(10))
    assert suncal.SolarCalibration(None).factor(at(10)) == 1.0


def test_intet_forbrug_under_grundforbruget():
    start = datetime(2026, 10, 26, tzinfo=TZ)   # mandag
    curve = [0.3] * 6 + [0.4] * 2 + [0.1, 0.02] + [0.5] * 14
    hours = [(start + timedelta(days=d, hours=h), curve[h]) for d in range(5) for h in range(24)]
    p = profile.build_profile(hours)
    assert abs(p.weekday[9] - 0.3) < 1e-9 and abs(p.weekday[8] - 0.3) < 1e-9
    assert abs(p.weekday[10] - 0.5) < 1e-9   # resten af dagen er uændret


def test_open_meteo_er_foerstevalg_og_timer_laeses_fra_start():
    const = _load("const")
    weather = _load("weather")
    assert const.FORECAST_SOURCES[0] == ("open_meteo_solar_forecast", False)
    hours = weather.solar_by_hour({"2026-11-04T08:00:00+01:00": 278.25, "2026-11-04T09:00:00+01:00": 623.5}, TZ, False)
    assert hours[datetime(2026, 11, 4, 8, tzinfo=TZ)] == 0.27825
    assert hours[datetime(2026, 11, 4, 9, tzinfo=TZ)] == 0.6235


def test_justering_gaelder_baade_i_dag_og_i_morgen():
    weather = _load("weather")
    cal, actual = fill(12, {9: 1.0, 11: 1.0}, {9: 0.2, 11: 2.0})
    cal.learn(actual, NOW)
    now = datetime(2026, 11, 21, 6, tzinfo=TZ)
    wh = {datetime(2026, 11, d, h, tzinfo=TZ).isoformat(): 1000.0 for d in (21, 22) for h in (10, 12)}
    raw = weather.summarize_forecast(wh, now, TZ, False)
    adj = weather.summarize_forecast(wh, now, TZ, False, scale=cal.factor)
    expect = 1.0 * cal.factor(datetime(2026, 11, 21, 10, tzinfo=TZ)) + 1.0 * cal.factor(datetime(2026, 11, 21, 12, tzinfo=TZ))
    assert raw["today_kwh"] == raw["tomorrow_kwh"] == 2.0
    assert abs(adj["today_kwh"] - expect) < 0.01 and abs(adj["tomorrow_kwh"] - expect) < 0.01
    assert adj["hours"][1]["kw"] > 1.0 > adj["hours"][0]["kw"]

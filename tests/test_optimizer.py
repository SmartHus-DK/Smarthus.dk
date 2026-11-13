"""Samlet batteriplan (optimizer.py)."""
import importlib.util, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("smarthus_optimizer",
                                              Path(__file__).parents[1] / "custom_components/smarthus/optimizer.py")
opt = importlib.util.module_from_spec(spec); sys.modules["smarthus_optimizer"] = opt; spec.loader.exec_module(opt)

Q = timedelta(minutes=15)
D0 = datetime(2026, 11, 13, tzinfo=timezone.utc)


def slots(hourly, prof, sun=None, start_h=0):
    out = []
    for h, p in hourly.items():
        for i in range(4):
            t = D0 + timedelta(hours=h) + i * Q
            out.append(opt.OptSlot(t, t + Q, p, p * 0.6, prof[h % 24] / 4, (sun or {}).get(h, 0) / 4))
    return out


def summary(sl, res, cap):
    return {(s.start - D0).total_seconds() // 3600: a for s, a in zip(sl, res.actions)}


KP = [0.82, 0.76, 0.85, 0.66, 0.74, 0.71, 1.04, 0.82, 0.94, 1.01, 0.66, 1.11, 1.23, 1.68, 1.59, 1.11, 1.64, 1.69,
      1.81, 1.59, 1.61, 0.89, 1.61, 0.96]


def test_flat_night_is_not_charged_and_battery_saved_for_evening():
    """Karsten 13/11 kl. 22: flad nat (ca. 2,0 kr) – ingen opladning hen over natten, batteriet gemmes til
    aftenens top (2,6–2,9 kr) i stedet for at blive brugt til 2,0–2,2 kr."""
    hourly = {22: 2.18, 23: 2.04, 24: 2.11, 25: 2.05, 26: 2.01, 27: 2.02, 28: 2.00, 29: 1.99, 30: 2.17, 31: 2.27,
              32: 2.29, 33: 2.18, 34: 2.02, 35: 1.88, 36: 1.86, 37: 1.96, 38: 2.14, 39: 2.27, 40: 2.39, 41: 2.91,
              42: 2.77, 43: 2.71, 44: 2.62, 45: 2.11, 46: 2.04, 47: 2.01}
    sun = {32: 0.3, 33: 0.7, 34: 1.2, 35: 1.5, 36: 1.4, 37: 1.0, 38: 0.4}
    sl = slots(hourly, KP, sun)
    r = opt.optimize(sl, 13.8 * 0.357, floor=0.69, full=13.8, charge_kw=3.8, min_spread=0.5)
    by = {}
    for s, a in zip(sl, r.actions):
        by.setdefault(int((s.start - D0).total_seconds() // 3600), set()).add(a)
    assert not any("charge" in by[h] for h in range(22, 31))          # ingen netopladning om natten
    assert all(by[h] == {"hold"} for h in range(23, 30))             # batteriet gemmes
    assert all(by[h] == {"use"} for h in range(41, 44))              # og bruges i aftenens top


def test_cheap_night_expensive_morning_charges_at_night():
    """Billig nat (1,0 kr), dyr morgen og aften (3,0 kr): lad om natten, brug det i de dyre timer."""
    hourly = {h: (1.0 if 0 <= h % 24 < 5 else 3.0) for h in range(22, 46)}
    sl = slots(hourly, [0.5] * 24)
    r = opt.optimize(sl, 1.0, floor=0.5, full=10.0, charge_kw=3.0, min_spread=0.5)
    acts = {int((s.start - D0).total_seconds() // 3600) % 24: a for s, a in zip(sl, r.actions)}
    assert any(a == "charge" for s, a in zip(sl, r.actions) if (s.start - D0).total_seconds() // 3600 % 24 < 5)
    assert r.levels[[i for i, s in enumerate(sl) if s.start == D0 + timedelta(hours=29)][0]] > 8.0
    assert acts[7] == "use"


def test_min_spread_blocks_marginal_charging():
    """Prisforskel 0,4 kr inkl. tab under min. prisforskel 0,5: ingen opladning fra nettet."""
    hourly = {h: (2.0 if h < 30 else 2.65) for h in range(22, 46)}
    sl = slots(hourly, [0.5] * 24)
    r = opt.optimize(sl, 1.0, floor=0.5, full=10.0, charge_kw=3.0, min_spread=0.5)
    assert "charge" not in r.actions
    r0 = opt.optimize(sl, 1.0, floor=0.5, full=10.0, charge_kw=3.0, min_spread=0.0)
    assert "charge" in r0.actions                                       # uden krav: lad (2,0/0,9 < 2,65)


def test_without_hold_support_and_buffer():
    hourly = {h: 2.0 for h in range(0, 12)}
    sl = slots(hourly, [0.4] * 24)
    r = opt.optimize(sl, 3.0, floor=0.5, full=10.0, charge_kw=3.0, can_hold=False)
    assert "hold" not in r.actions
    before = sum(s.load for s in sl)
    opt.morning_buffer(sl, timezone.utc, 2.0)
    assert abs(sum(s.load for s in sl) - before - 2.0) < 1e-6

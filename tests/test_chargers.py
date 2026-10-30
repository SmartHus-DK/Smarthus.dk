"""Test af færdige opsætninger for ladestandere: findes entiteterne ud fra integrationernes unique_id'er?"""
import importlib.util, sys, types
from pathlib import Path

PKG = Path(__file__).parents[1] / "custom_components" / "smarthus"
pkg = sys.modules.get("smarthus") or types.ModuleType("smarthus"); pkg.__path__ = [str(PKG)]; sys.modules["smarthus"] = pkg
for n in ("presets", "chargers", "plug"):
    spec = importlib.util.spec_from_file_location(f"smarthus.{n}", PKG / f"{n}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[f"smarthus.{n}"] = m; spec.loader.exec_module(m)
cz, pz, plug = sys.modules["smarthus.chargers"], sys.modules["smarthus.presets"], sys.modules["smarthus.plug"]
E = pz.Entry

# unique_id'er som integrationerne laver dem (fra deres kildekode)
SAMPLES = {
    "easee": [E("switch.lader_aktiveret", "EH123456_is_enabled"), E("switch.lader_smart", "EH123456_smart_charging"),
              E("sensor.lader_status", "EH123456_status"), E("sensor.lader_effekt", "EH123456_power"),
              E("sensor.lader_total", "EH123456_lifetime_energy"), E("sensor.lader_session", "EH123456_session_energy")],
    "zaptec": [E("switch.zap_lader", "abc_charger_operation_mode"), E("sensor.zap_mode", "abc_charger_operation_mode"),
               E("sensor.zap_effekt", "abc_total_charge_power"), E("sensor.zap_session", "abc_total_charge_power_session"),
               E("sensor.zap_meter", "abc_signed_meter_value_kwh")],
    "wallbox": [E("switch.wb_pause", "paused-12345"), E("sensor.wb_status", "status_description-12345"),
                E("sensor.wb_effekt", "charging_power-12345"), E("sensor.wb_energi", "added_energy-12345"),
                E("sensor.wb_groen", "added_green_energy-12345"), E("number.wb_strom", "max_charging_current-12345"),
                E("number.wb_icp", "icp_max_current-12345")],
    "wattpilot": [E("select.wp_force", "WP1-frc"), E("sensor.wp_car", "WP1-car"), E("sensor.wp_conn", "WP1-carConnected"),
                  E("sensor.wp_nrg", "WP1-nrg"), E("sensor.wp_eto", "WP1-eto"), E("number.wp_amp", "WP1-amp")],
    "goe": [E("select.goe_frc", "goecharger_api2.goe_123_frc"), E("sensor.goe_car", "goecharger_api2.goe_123_car"),
            E("sensor.goe_car_value", "goecharger_api2.goe_123_car_value"), E("sensor.goe_p", "goecharger_api2.goe_123_nrg_11"),
            E("sensor.goe_p1", "goecharger_api2.goe_123_nrg_1"), E("sensor.goe_eto", "goecharger_api2.goe_123_eto"),
            E("number.goe_amp", "goecharger_api2.goe_123_amp")],
    "garo": [E("select.garo", "charger_99-sensor"), E("sensor.garo_status", "charger_99-status"),
             E("sensor.garo_cs", "charger_99-charge_status"), E("sensor.garo_p", "charger_99-current_charging_power"),
             E("sensor.garo_e", "charger_99-acc_energy"), E("sensor.garo_se", "charger_99-acc_session_energy"),
             E("number.garo_i", "charger_99-current_limit")],
    "chargeamps": [E("switch.ca", "chargeamps_cp1_1_enable"), E("binary_sensor.ca_cable", "chargeamps_cp1_1_cable_connected"),
                   E("sensor.ca_p", "chargeamps_cp1_1_power"), E("sensor.ca_e", "chargeamps_cp1_1_total_energy"),
                   E("number.ca_i", "chargeamps_cp1_1_max_current")],
    "alfen": [E("switch.alfen", "dev1_charging"), E("sensor.alfen_status", "dev1-status_socket_1"),
              E("sensor.alfen_p", "dev1-active_power_total_socket_1"), E("sensor.alfen_e", "dev1-meter_reading_socket_1")],
    "monta": [E("switch.monta", "entry_77_charger"), E("binary_sensor.monta_kabel", "entry_77_cable_plugged_in"),
              E("sensor.monta_e", "entry_77_charger_lastMeterReadingKwh")],
    "ocpp": [E("switch.cp_charge", "switch.ocpp.cp1.charge_control"), E("switch.cp_av", "switch.ocpp.cp1.availability"),
             E("sensor.cp_status", "ocpp.cp1.status_connector.sensor"), E("sensor.cp_p", "ocpp.cp1.power_active_import.sensor"),
             E("sensor.cp_e", "ocpp.cp1.energy_active_import_register.sensor"), E("number.cp_i", "number.ocpp.cp1.maximum_current")],
}


def test_all_presets_have_samples():
    assert set(SAMPLES) == set(cz.CHARGERS)


def test_match_each_charger():
    for key, entries in SAMPLES.items():
        p = cz.CHARGERS[key]
        m = cz.match(p, entries, entries + [E("number.zap_inst_strom", "inst1_available_current")])
        assert not m.missing, (key, m.missing)
        assert set(m.fields) == {cz.ROLE_FIELDS[r] for r in p.roles}, key
    f = cz.match(cz.CHARGERS["easee"], SAMPLES["easee"]).fields
    assert f == {"charger": "switch.lader_aktiveret", "plug": "sensor.lader_status", "power": "sensor.lader_effekt",
                 "energy": "sensor.lader_total"}
    assert cz.match(cz.CHARGERS["wallbox"], SAMPLES["wallbox"]).fields["energy"] == "sensor.wb_energi"
    assert cz.match(cz.CHARGERS["goe"], SAMPLES["goe"]).fields["power"] == "sensor.goe_p"
    assert cz.match(cz.CHARGERS["garo"], SAMPLES["garo"]).fields["plug"] == "sensor.garo_status"
    assert cz.match(cz.CHARGERS["zaptec"], SAMPLES["zaptec"]).fields["power"] == "sensor.zap_effekt"


def test_entry_wide_and_disabled():
    p = cz.CHARGERS["zaptec"]
    charger = SAMPLES["zaptec"]
    inst = [E("number.zap_inst_strom", "inst1_available_current")]
    assert "current" in cz.match(p, charger).missing
    m = cz.match(p, charger, charger + inst)
    assert m.fields["current_entity"] == "number.zap_inst_strom"
    wp = [E(e.entity_id, e.unique_id, None, e.entity_id == "select.wp_force") for e in SAMPLES["wattpilot"]]
    m = cz.match(cz.CHARGERS["wattpilot"], wp)
    assert m.disabled == ["select.wp_force"] and "charger" in m.missing


def test_car_fields():
    m = cz.match(cz.CHARGERS["goe"], SAMPLES["goe"])
    f = cz.car_fields(m, "dev1")
    assert f["charger"] == "select.goe_frc" and f["charger_on"] == "2" and f["charger_off"] == "1"
    assert "easee_device" not in f
    f = cz.car_fields(cz.match(cz.CHARGERS["easee"], SAMPLES["easee"]), "dev9")
    assert f["easee_device"] == "dev9" and f["charger_on"] is None


def test_plug_states():
    s = plug.plug_state
    assert s("disconnected") is False and s("connected_charging") is True and s("connected_requesting") is True
    assert s("Idle") is False and s("Wait for car") is True and s("Wait Car") is True     # go-e / Wattpilot
    assert s("Available") is False and s("Preparing") is True and s("SuspendedEV") is True  # OCPP
    assert s("NOT_CONNECTED") is False and s("CONNECTED") is True and s("SEARCH_COMM") is False  # Garo
    assert s("Ready") is True and s("Ready", ("ready", "locked")) is False                 # Wallbox: Ready = ingen bil
    assert s("Waiting for car demand", ("ready",)) is True

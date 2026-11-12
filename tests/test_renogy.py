from tower_monitor.sensors.renogy import REG_COUNT, RenogySensor, decode, decode_faults


def regs(**at):
    r = [0] * REG_COUNT
    for addr, val in at.items():
        r[int(addr[1:], 16) - 0x100] = val
    return r


def test_decode_basic():
    out = decode(regs(r0100=91, r0101=131, r0102=245, r0103=(25 << 8) | 0x85,
                      r0107=190, r0109=32, r0113=140, r0120=5))
    assert out["battery_soc"] == 91
    assert out["battery_voltage"] == 13.1
    assert out["charge_current"] == 2.45
    assert out["controller_temp"] == 25
    assert out["battery_temp"] == -5          # sign bit set
    assert out["solar_voltage"] == 19.0
    assert out["solar_power"] == 32
    assert out["generation_today"] == 140
    assert out["charging_state"] == "float"
    assert out["faults"] == "none" and out["fault"] == "OFF"


def test_decode_faults():
    code = (1 << 16) | (1 << 21)
    assert decode_faults(code) == ["controller temperature too high", "battery over-discharge"]
    out = decode(regs(r0121=code >> 16, r0122=code & 0xFFFF))
    assert out["fault"] == "ON"
    assert "battery over-discharge" in out["faults"]


def test_entities_match_readings():
    s = RenogySensor({}, simulate=True)
    assert {e.key for e in s.entities()} == set(s.read())

import pytest

from tower_monitor.sensors.environment import EnvironmentSensor, crc8, dew_point, sht4x_convert


def _frame(t_ticks, rh_ticks):
    t = bytes([t_ticks >> 8, t_ticks & 0xFF])
    h = bytes([rh_ticks >> 8, rh_ticks & 0xFF])
    return t + bytes([crc8(t)]) + h + bytes([crc8(h)])


def test_crc8_datasheet_example():
    # Sensirion datasheet: CRC of 0xBEEF is 0x92
    assert crc8(b"\xbe\xef") == 0x92


def test_sht4x_convert():
    # 25 °C -> ticks = (25 + 45) / 175 * 65535 = 26214
    temp, rh = sht4x_convert(_frame(26214, 36700))
    assert temp == pytest.approx(25.0, abs=0.01)
    assert rh == pytest.approx(64.0, abs=0.1)


def test_sht4x_bad_crc():
    raw = bytearray(_frame(26214, 36700))
    raw[2] ^= 0xFF
    with pytest.raises(ValueError, match="CRC"):
        sht4x_convert(bytes(raw))


def test_dew_point_sane():
    assert dew_point(20.0, 50.0) == pytest.approx(9.3, abs=0.2)


def test_simulated_read_has_all_entities():
    s = EnvironmentSensor({"driver": "bme280"}, simulate=True)
    s.setup()
    data = s.read()
    assert {e.key for e in s.entities()} == set(data)

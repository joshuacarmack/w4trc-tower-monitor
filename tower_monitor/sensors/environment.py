"""Shack temperature / humidity over I2C.

Drivers:
  sht4x  - Sensirion SHT40/41/45 (default address 0x44). Read directly with
           smbus2, no Adafruit Blinka needed.
  bme280 - Bosch BME280 (0x76 or 0x77) via the RPi.bme280 package. The chip
           ID is checked first so a BMP280 (no humidity, often sold as a
           "BME280") gives a clear error instead of garbage.

Temperatures are published in °C; HA converts to °F for display.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

from .base import Entity, Sensor

SHT4X_MEASURE_HIGH = 0xFD
BME280_CHIP_ID_REG = 0xD0
CHIP_IDS = {0x60: "BME280", 0x58: "BMP280", 0x56: "BMP280", 0x57: "BMP280", 0x61: "BME680"}


def crc8(data: bytes) -> int:
    """Sensirion CRC-8 (poly 0x31, init 0xFF)."""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def sht4x_convert(raw: bytes) -> tuple[float, float]:
    """6 raw bytes -> (temp °C, RH %). Raises ValueError on CRC mismatch."""
    if len(raw) != 6:
        raise ValueError(f"expected 6 bytes, got {len(raw)}")
    if crc8(raw[0:2]) != raw[2] or crc8(raw[3:5]) != raw[5]:
        raise ValueError("SHT4x CRC mismatch (wiring noise or cable too long?)")
    t_ticks = raw[0] << 8 | raw[1]
    rh_ticks = raw[3] << 8 | raw[4]
    temp = -45 + 175 * t_ticks / 65535
    rh = min(100.0, max(0.0, -6 + 125 * rh_ticks / 65535))
    return temp, rh


def dew_point(temp_c: float, rh: float) -> float:
    """Magnus formula. Useful for spotting condensation risk in the shack."""
    a, b = 17.62, 243.12
    rh = max(rh, 0.1)
    g = math.log(rh / 100) + a * temp_c / (b + temp_c)
    return b * g / (a - g)


class EnvironmentSensor(Sensor):
    group = "environment"
    title = "Environment"

    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        self.driver = cfg.get("driver", "sht4x").lower()
        if self.driver not in ("sht4x", "bme280"):
            raise ValueError(f"environment.driver must be sht4x or bme280, not {self.driver!r}")
        default_addr = 0x44 if self.driver == "sht4x" else 0x76
        self.address = int(cfg.get("address") or default_addr)
        self.bus_num = int(cfg.get("i2c_bus", 1))
        self._bus = None
        self._bme_params = None

    def entities(self):
        ents = [
            Entity("temperature", "Shack temperature", unit="°C", device_class="temperature",
                   state_class="measurement", precision=1),
            Entity("humidity", "Shack humidity", unit="%", device_class="humidity",
                   state_class="measurement", precision=0),
            Entity("dew_point", "Shack dew point", unit="°C", device_class="temperature",
                   state_class="measurement", precision=1),
        ]
        if self.driver == "bme280":
            ents.append(Entity("pressure", "Shack pressure", unit="hPa",
                               device_class="atmospheric_pressure",
                               state_class="measurement", precision=1))
        return ents

    def setup(self):
        if self.simulate:
            return
        from smbus2 import SMBus
        try:
            self._bus = SMBus(self.bus_num)
        except FileNotFoundError as e:
            raise RuntimeError(f"/dev/i2c-{self.bus_num} missing; enable I2C in raspi-config") from e
        if self.driver == "bme280":
            try:
                chip = self._bus.read_byte_data(self.address, BME280_CHIP_ID_REG)
            except OSError as e:
                raise RuntimeError(
                    f"no device at 0x{self.address:02x} on i2c-{self.bus_num} "
                    f"(check wiring; try address 0x76/0x77; run i2cdetect -y {self.bus_num})"
                ) from e
            if chip != 0x60:
                name = CHIP_IDS.get(chip, "unknown chip")
                raise RuntimeError(f"chip id 0x{chip:02x} is {name}, not a BME280")
            import bme280  # RPi.bme280
            self._bme_params = bme280.load_calibration_params(self._bus, self.address)

    def read(self) -> dict[str, Any]:
        if self.simulate:
            temp = 18 + random.uniform(-1, 1)
            rh = 55 + random.uniform(-5, 5)
            out = {"temperature": round(temp, 2), "humidity": round(rh, 1)}
            if self.driver == "bme280":
                out["pressure"] = round(1013 + random.uniform(-3, 3), 1)
        elif self.driver == "sht4x":
            temp, rh = self._read_sht4x()
            out = {"temperature": round(temp, 2), "humidity": round(rh, 1)}
        else:
            import bme280
            s = bme280.sample(self._bus, self.address, self._bme_params)
            temp, rh = s.temperature, s.humidity
            out = {"temperature": round(temp, 2), "humidity": round(rh, 1),
                   "pressure": round(s.pressure, 1)}
        out["dew_point"] = round(dew_point(temp, rh), 1)
        return out

    def _read_sht4x(self) -> tuple[float, float]:
        from smbus2 import i2c_msg
        try:
            self._bus.i2c_rdwr(i2c_msg.write(self.address, [SHT4X_MEASURE_HIGH]))
            time.sleep(0.01)
            msg = i2c_msg.read(self.address, 6)
            self._bus.i2c_rdwr(msg)
        except OSError as e:
            raise RuntimeError(
                f"no response from SHT4x at 0x{self.address:02x} "
                f"(check wiring; run i2cdetect -y {self.bus_num})"
            ) from e
        return sht4x_convert(bytes(msg))

    def close(self):
        if self._bus is not None:
            self._bus.close()
            self._bus = None

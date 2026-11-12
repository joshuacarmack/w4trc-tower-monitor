"""Renogy Rover charge controller over its RJ12 RS232 port (Modbus RTU).

Hardware: USB-RS232 adapter (FTDI) -> DB9 -> RJ12 into the Rover's RS232
port. Some RJ12 pins on the Rover carry supply voltage: meter the port and
check the pinout before connecting the cable.

Protocol: 9600 8N1, slave id 1 (some units answer on 255 or 16). One read
of holding registers 0x0100-0x0122 covers everything below. Register map
is from Renogy's "ROVER Modbus protocol" document; verify values against
the controller's own display on the bench.
"""

from __future__ import annotations

import random
from typing import Any

from .base import Entity, Sensor

START_REG = 0x0100
REG_COUNT = 0x0122 - 0x0100 + 1   # 35

CHARGING_STATES = {
    0: "off", 1: "activated", 2: "mppt", 3: "equalizing",
    4: "boost", 5: "float", 6: "current limiting",
}

FAULT_BITS = {
    30: "charge MOS short circuit",
    29: "anti-reverse MOS short circuit",
    28: "solar panel reversed",
    27: "solar working point over-voltage",
    26: "solar counter-current",
    25: "PV input over-voltage",
    24: "PV input short circuit",
    23: "PV input overpower",
    22: "ambient temperature too high",
    21: "controller temperature too high",
    20: "load overpower/over-current",
    19: "load short circuit",
    18: "battery under-voltage warning",
    17: "battery over-voltage",
    16: "battery over-discharge",
}


def _signed_temp(byte: int) -> int:
    """Rover temps: bit 7 = sign, bits 0-6 = magnitude in °C."""
    return -(byte & 0x7F) if byte & 0x80 else byte & 0x7F


def decode_faults(code: int) -> list[str]:
    return [name for bit, name in sorted(FAULT_BITS.items(), reverse=True) if code & (1 << bit)]


def decode(regs: list[int]) -> dict[str, Any]:
    if len(regs) < REG_COUNT:
        raise ValueError(f"expected {REG_COUNT} registers, got {len(regs)}")

    def r(addr: int) -> int:
        return regs[addr - START_REG]

    temps = r(0x0103)
    faults_code = (r(0x0121) << 16) | r(0x0122)
    faults = decode_faults(faults_code)
    state_code = r(0x0120) & 0xFF
    return {
        "battery_soc": r(0x0100),
        "battery_voltage": round(r(0x0101) * 0.1, 1),
        "charge_current": round(r(0x0102) * 0.01, 2),
        "controller_temp": _signed_temp(temps >> 8),
        "battery_temp": _signed_temp(temps & 0xFF),
        "load_voltage": round(r(0x0104) * 0.1, 1),
        "load_current": round(r(0x0105) * 0.01, 2),
        "load_power": r(0x0106),
        "solar_voltage": round(r(0x0107) * 0.1, 1),
        "solar_current": round(r(0x0108) * 0.01, 2),
        "solar_power": r(0x0109),
        "battery_min_voltage_today": round(r(0x010B) * 0.1, 1),
        "battery_max_voltage_today": round(r(0x010C) * 0.1, 1),
        "charge_ah_today": r(0x0111),
        "discharge_ah_today": r(0x0112),
        "generation_today": r(0x0113),
        "charging_state": CHARGING_STATES.get(state_code, f"unknown ({state_code})"),
        "faults": ", ".join(faults) if faults else "none",
        "fault": "ON" if faults else "OFF",
    }


class RenogySensor(Sensor):
    group = "renogy"
    title = "Solar controller"

    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        self._inst = None

    def entities(self):
        V, A, W = "V", "A", "W"
        m = "measurement"
        return [
            Entity("battery_voltage", "Battery voltage", unit=V, device_class="voltage",
                   state_class=m, precision=1),
            Entity("battery_soc", "Battery charge (controller estimate)", unit="%",
                   device_class="battery", state_class=m),
            Entity("charge_current", "Charge current", unit=A, device_class="current",
                   state_class=m, precision=2),
            Entity("solar_voltage", "Solar panel voltage", unit=V, device_class="voltage",
                   state_class=m, precision=1),
            Entity("solar_current", "Solar panel current", unit=A, device_class="current",
                   state_class=m, precision=2),
            Entity("solar_power", "Solar power", unit=W, device_class="power", state_class=m),
            Entity("load_voltage", "Load voltage", unit=V, device_class="voltage",
                   state_class=m, precision=1, diagnostic=True),
            Entity("load_current", "Load current", unit=A, device_class="current",
                   state_class=m, precision=2),
            Entity("load_power", "Load power", unit=W, device_class="power", state_class=m),
            Entity("controller_temp", "Controller temperature", unit="°C",
                   device_class="temperature", state_class=m, diagnostic=True),
            Entity("battery_temp", "Battery temperature (controller)", unit="°C",
                   device_class="temperature", state_class=m, diagnostic=True),
            Entity("battery_min_voltage_today", "Battery min voltage today", unit=V,
                   device_class="voltage", precision=1),
            Entity("battery_max_voltage_today", "Battery max voltage today", unit=V,
                   device_class="voltage", precision=1),
            Entity("charge_ah_today", "Charge today", unit="Ah", icon="mdi:battery-plus",
                   state_class="total_increasing"),
            Entity("discharge_ah_today", "Discharge today", unit="Ah", icon="mdi:battery-minus",
                   state_class="total_increasing"),
            Entity("generation_today", "Solar energy today", unit="Wh", device_class="energy",
                   state_class="total_increasing"),
            Entity("charging_state", "Charging state", icon="mdi:solar-power"),
            Entity("faults", "Controller faults", icon="mdi:alert"),
            Entity("fault", "Controller fault", component="binary_sensor",
                   device_class="problem"),
        ]

    def setup(self):
        if self.simulate:
            return
        import minimalmodbus
        import serial
        port = self.cfg.get("port") or ""
        if not port:
            # Never guess /dev/ttyUSBn: the Sixfab modem owns ttyUSB0-4.
            raise RuntimeError("renogy.port not set; use the /dev/serial/by-id/usb-FTDI_... path")
        inst = minimalmodbus.Instrument(port, int(self.cfg.get("slave_id", 1)))
        inst.serial.baudrate = int(self.cfg.get("baudrate", 9600))
        inst.serial.bytesize = 8
        inst.serial.parity = serial.PARITY_NONE
        inst.serial.stopbits = 1
        inst.serial.timeout = 2
        inst.clear_buffers_before_each_transaction = True
        inst.close_port_after_each_call = False
        self._inst = inst

    def read(self) -> dict[str, Any]:
        if self.simulate:
            regs = [0] * REG_COUNT
            regs[0x00] = 87
            regs[0x01] = int(132 + random.randint(-3, 3))   # 13.2 V
            regs[0x02] = 150                                 # 1.50 A
            regs[0x03] = (22 << 8) | 20
            regs[0x07] = 182
            regs[0x08] = 120
            regs[0x09] = 21
            regs[0x0B], regs[0x0C] = 124, 143
            regs[0x13] = 85
            regs[0x20] = 2
            return decode(regs)
        try:
            regs = self._inst.read_registers(START_REG, REG_COUNT, functioncode=3)
        except Exception as e:
            raise RuntimeError(
                f"no Modbus reply from Rover (port {self.cfg.get('port')}, "
                f"id {self.cfg.get('slave_id')}): {e}"
            ) from e
        return decode(regs)

    def close(self):
        if self._inst is not None:
            try:
                self._inst.serial.close()
            except Exception:
                pass
            self._inst = None

"""Configuration loading.

Config is a TOML file (default /etc/tower-monitor/config.toml). Anything not
set in the file falls back to DEFAULTS below, so the file only needs the
settings you actually change plus the MQTT credentials.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("/etc/tower-monitor/config.toml")

DEFAULTS: dict[str, Any] = {
    "device": {
        "id": "tower_monitor",          # HA unique_id prefix; letters/digits/_ only
        "name": "Tower Monitor",        # HA device name
        "topic_prefix": "tower",        # all state topics live under this
        "state_dir": "/var/lib/tower-monitor",
    },
    "mqtt": {
        "host": "localhost",
        "port": 1883,
        "username": "",
        "password": "",
        "client_id": "tower-monitor",
        "keepalive": 300,               # seconds; long to save cellular data
        "tls": False,
        "ca_certs": "",                 # path to CA bundle if tls = true and not a public CA
        "discovery_prefix": "homeassistant",
    },
    "environment": {
        "enabled": True,
        "driver": "sht4x",              # "sht4x" or "bme280"
        "i2c_bus": 1,
        "address": 0,                   # 0 = driver default (sht4x 0x44, bme280 0x76)
        "interval": 600,
    },
    "system": {
        "enabled": True,
        "interval": 1800,
    },
    "cellular": {
        "enabled": True,
        "interval": 1800,
    },
    "data_usage": {
        "enabled": True,
        "interface": "wwan0",
        "interval": 3600,
    },
    "mains": {
        "enabled": True,
        "gpio": 23,                     # pin 16. Not 27: the Sixfab Base HAT's user LED
        # Optocoupler modules pull the pin LOW when their input is powered.
        "present_when_low": True,
        "debounce": 2.0,                # seconds
        "interval": 900,                # re-send even when unchanged, for expire_after
    },
    "door": {
        "enabled": True,
        "gpio": 17,
        # Reed switch wired GPIO -> switch -> GND with pull-up: closed door = LOW.
        "closed_when_low": True,
        "debounce": 0.1,
        "interval": 900,
    },
    "renogy": {
        "enabled": True,
        "port": "",                     # /dev/serial/by-id/usb-FTDI_... (modem owns ttyUSB0-4)
        "baudrate": 9600,
        "slave_id": 1,
        "interval": 600,
    },
    "camera": {
        "enabled": True,
        "device": "/dev/video0",
        "capture_resolution": "1280x720",
        "skip_frames": 30,              # let auto-exposure / IR settle
        "delay": 2.0,                   # seconds after door opens before first shot
        "burst": 3,                     # photos saved locally per event
        "burst_interval": 1.5,
        "send_width": 640,              # resized copy sent over MQTT
        "send_quality": 60,             # JPEG quality of the sent copy
        "max_sent_per_day": 3,          # hard cap on photos sent over cellular
        "storage_dir": "/var/lib/tower-monitor/photos",
        "keep_days": 120,
        "interval": 1800,               # how often photo counters are re-sent
    },
    "tailscale": {
        # HA buttons to turn Tailscale on/off. Needs scripts/tower-tailscale
        # and its systemd units (install.sh). State changes are sent at once.
        "enabled": True,
        "interval": 1800,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path) if path else DEFAULT_PATH
    with open(path, "rb") as f:
        user = tomllib.load(f)
    return _merge(DEFAULTS, user)


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS)

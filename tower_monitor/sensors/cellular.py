"""Cellular modem stats via ModemManager (mmcli).

Each field is parsed on its own so one missing value (e.g. no access
technology while re-registering) doesn't throw away the rest.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from typing import Any

from .base import Entity, Sensor


def find_modem_index(list_output: str) -> str | None:
    m = re.search(r"/Modem/(\d+)", list_output)
    return m.group(1) if m else None


def _clean(v: Any) -> Any:
    return None if v in (None, "", "--", "unknown") else v


def parse_modem_json(text: str) -> dict[str, Any]:
    modem = json.loads(text).get("modem", {})
    generic = modem.get("generic", {})
    gpp = modem.get("3gpp", {})
    out: dict[str, Any] = {}

    sq = generic.get("signal-quality", {})
    try:
        out["signal_quality"] = int(sq.get("value"))
    except (TypeError, ValueError):
        pass
    tech = generic.get("access-technologies") or []
    if tech and _clean(tech[0]):
        out["access_tech"] = ",".join(tech)
    for key, src in (("state", generic.get("state")),
                     ("operator", gpp.get("operator-name")),
                     ("registration", gpp.get("registration-state"))):
        if _clean(src) is not None:
            out[key] = src
    return out


class CellularSensor(Sensor):
    group = "cellular"
    title = "Cellular"

    def entities(self):
        return [
            Entity("signal_quality", "Cell signal", unit="%", state_class="measurement",
                   icon="mdi:signal-cellular-3", diagnostic=True),
            Entity("access_tech", "Cell technology", icon="mdi:signal-4g", diagnostic=True),
            Entity("operator", "Cell operator", icon="mdi:radio-tower", diagnostic=True),
            Entity("state", "Modem state", icon="mdi:cellphone-information", diagnostic=True),
            Entity("registration", "Cell registration", icon="mdi:sim", diagnostic=True),
        ]

    def read(self) -> dict[str, Any]:
        if self.simulate:
            return {"signal_quality": random.randint(40, 80), "access_tech": "lte",
                    "operator": "SIMULATED", "state": "connected", "registration": "home"}
        r = subprocess.run(["mmcli", "-L"], capture_output=True, text=True, timeout=15)
        idx = find_modem_index(r.stdout)
        if idx is None:
            raise RuntimeError("no modem found by ModemManager (mmcli -L)")
        r = subprocess.run(["mmcli", "-m", idx, "--output-json"],
                           capture_output=True, text=True, timeout=15, check=True)
        data = parse_modem_json(r.stdout)
        if not data:
            raise RuntimeError("mmcli returned no usable fields")
        return data

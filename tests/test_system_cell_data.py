import json
from datetime import date

import pytest

from tower_monitor.sensors.cellular import find_modem_index, parse_modem_json
from tower_monitor.sensors.data_usage import parse_vnstat
from tower_monitor.sensors.system import UNDERVOLT_SINCE_BOOT, UNDERVOLT_NOW, parse_throttled


def test_parse_throttled():
    flags = parse_throttled("throttled=0x50005\n")
    assert flags & UNDERVOLT_NOW
    assert flags & UNDERVOLT_SINCE_BOOT


def test_find_modem_index():
    out = "    /org/freedesktop/ModemManager1/Modem/3 [Quectel] EC25\n"
    assert find_modem_index(out) == "3"
    assert find_modem_index("No modems were found") is None


def test_parse_modem_json_tolerates_missing_fields():
    doc = {"modem": {
        "generic": {"signal-quality": {"value": "67", "recent": "yes"},
                    "access-technologies": [], "state": "registered"},
        "3gpp": {"operator-name": "--", "registration-state": "home"},
    }}
    out = parse_modem_json(json.dumps(doc))
    assert out == {"signal_quality": 67, "state": "registered", "registration": "home"}


def test_parse_vnstat():
    doc = {"interfaces": [{"name": "wwan0", "traffic": {
        "day": [{"date": {"year": 2026, "month": 11, "day": 11}, "rx": 300_000, "tx": 200_000}],
        "month": [
            {"date": {"year": 2026, "month": 10}, "rx": 50_000_000, "tx": 1},
            {"date": {"year": 2026, "month": 11}, "rx": 7_000_000, "tx": 3_000_000},
        ],
    }}]}
    out = parse_vnstat(json.dumps(doc), "wwan0", date(2026, 11, 11))
    assert out == {"month_mb": 10.0, "today_mb": 0.5}


def test_parse_vnstat_wrong_interface():
    doc = {"interfaces": [{"name": "eth0", "traffic": {}}]}
    with pytest.raises(RuntimeError, match="has: eth0"):
        parse_vnstat(json.dumps(doc), "wwan0", date(2026, 11, 11))

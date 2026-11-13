from tower_monitor.mqtt import discovery_messages
from tower_monitor.sensors.tailscale import TailscaleSensor, parse_command, read_off_at

FULL = {"device": {"id": "tower_monitor", "name": "Tower Monitor", "topic_prefix": "tower"}}


def test_parse_command():
    assert parse_command(b"on") == "on"
    assert parse_command(b" OFF\n") == "off"
    assert parse_command(b"90") == "90"
    assert parse_command(b"007") == "7"
    for bad in (b"0", b"481", b"-5", b"rm -rf", b"", b"on; reboot"):
        assert parse_command(bad) is None


def test_read_off_at(tmp_path):
    p = tmp_path / "off"
    p.write_text("1763080200\n")
    assert read_off_at(p) == "2025-11-14T00:30:00+00:00"
    assert read_off_at(tmp_path / "missing") is None
    p.write_text("junk")
    assert read_off_at(p) is None


def test_command_writes_request_file(tmp_path):
    req = tmp_path / "tailscale-request"
    s = TailscaleSensor({"request_file": str(req)}, full_cfg=FULL)
    fired = []
    s.trigger = lambda: fired.append(1)
    s.handle_command(b"on")
    assert req.read_text() == "on\n"
    s.handle_command(b"bogus")
    assert req.read_text() == "on\n" and len(fired) == 1


def test_simulated_state():
    s = TailscaleSensor({}, simulate=True, full_cfg=FULL)
    assert s.read() == {"running": "OFF", "off_at": None}
    s.handle_command(b"30")
    assert s.read()["running"] == "ON"


def test_button_discovery():
    s = TailscaleSensor({}, full_cfg=FULL)
    msgs = dict(discovery_messages(s.entities(), group="tailscale", expire_after=5460,
                                   device_cfg=FULL["device"], discovery_prefix="homeassistant"))
    on = msgs["homeassistant/button/tower_monitor/tailscale_on/config"]
    assert on["command_topic"] == "tower/tailscale/cmd" and on["payload_press"] == "on"
    assert "state_topic" not in on
    run = msgs["homeassistant/binary_sensor/tower_monitor/tailscale_running/config"]
    assert run["state_topic"] == "tower/tailscale/state"

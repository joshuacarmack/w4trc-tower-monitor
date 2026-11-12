import json
import threading
import time

from tower_monitor import config, sensors
from tower_monitor.runner import Runner


class FakePublisher:
    def __init__(self):
        self.connected = threading.Event()
        self.json = {}
        self.raw = {}
        self.discovery = []
        self.subs = {}

    def start(self):
        self.connected.set()

    def stop(self):
        pass

    def add_discovery(self, msgs):
        self.discovery.extend(msgs)

    def subscribe(self, topic, handler):
        self.subs[topic] = handler

    def publish_json(self, topic, payload, **kw):
        self.json[topic] = payload

    def publish_raw(self, topic, payload, **kw):
        self.raw[topic] = payload


def test_full_simulated_cycle(tmp_path):
    cfg = config.defaults()
    cfg["device"]["state_dir"] = str(tmp_path)
    cfg["camera"].update(storage_dir=str(tmp_path / "photos"), delay=0, burst=1)
    sens = sensors.build(cfg, simulate=True)
    pub = FakePublisher()
    runner = Runner(cfg, sens, pub)

    t = threading.Thread(target=runner.run, daemon=True)
    t.start()
    deadline = time.time() + 5
    groups = {s.group for s in sens}
    while time.time() < deadline and len(pub.json) < len(groups):
        time.sleep(0.05)

    for g in groups:
        assert f"tower/{g}/state" in pub.json, g
        assert pub.raw[f"tower/{g}/status"] == "ok", g

    # Every entity key a sensor declares appears in its state payload.
    for s in sens:
        state = pub.json[f"tower/{s.group}/state"]
        for e in s.entities():
            if e.component in ("sensor", "binary_sensor"):
                assert e.key in state, (s.group, e.key)

    # Discovery is valid JSON-serialisable and unique.
    ids = [p["unique_id"] for _, p in pub.discovery]
    assert len(ids) == len(set(ids))
    json.dumps([p for _, p in pub.discovery])

    # HA button press -> photo published
    pub.subs["tower/camera/capture"](b"PRESS")
    deadline = time.time() + 5
    while time.time() < deadline and "tower/camera/image" not in pub.raw:
        time.sleep(0.05)
    assert pub.raw["tower/camera/image"][:2] == b"\xff\xd8"

    runner.stop()
    t.join(timeout=5)
    assert not t.is_alive()

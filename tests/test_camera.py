import threading
from datetime import date

from tower_monitor.sensors.camera import Camera, DailyCounter
from tower_monitor.state import StateStore


class FakePub:
    def __init__(self, connected=True):
        self.connected = threading.Event()
        if connected:
            self.connected.set()
        self.sent = []
        self.subs = {}

    def publish_raw(self, topic, payload, **kw):
        self.sent.append((topic, payload))

    def subscribe(self, topic, handler):
        self.subs[topic] = handler


def make_camera(tmp_path, **cfg):
    base = {"storage_dir": str(tmp_path / "photos"), "delay": 0, "burst": 3,
            "burst_interval": 0, "max_sent_per_day": 2}
    base.update(cfg)
    cam = Camera(base, simulate=True, full_cfg={"device": {"topic_prefix": "tower"}})
    cam.store = StateStore(tmp_path / "state.json")
    pub = FakePub()
    cam.attach(pub)
    cam.setup()
    return cam, pub


def capture_sync(cam):
    cam.request_capture()
    with cam._busy:   # wait for worker to finish
        pass


def test_capture_sends_one_small_photo(tmp_path):
    cam, pub = make_camera(tmp_path)
    capture_sync(cam)
    out = cam.read()
    assert out["stored"] == 3
    assert out["sent_today"] == 1
    assert out["last_photo"] is not None
    (topic, data), = pub.sent
    assert topic == "tower/camera/image"
    assert data[:2] == b"\xff\xd8"           # JPEG
    assert len(data) < 60_000
    # nothing pending: a second read sends nothing
    cam.read()
    assert len(pub.sent) == 1


def test_daily_limit(tmp_path):
    cam, pub = make_camera(tmp_path, max_sent_per_day=2)
    for _ in range(3):
        capture_sync(cam)
        cam.read()
    assert len(pub.sent) == 2
    assert cam.read()["stored"] == 9         # still saved locally


def test_offline_photo_sent_after_reconnect(tmp_path):
    cam, pub = make_camera(tmp_path)
    pub.connected.clear()
    capture_sync(cam)
    cam.read()
    assert pub.sent == []
    pub.connected.set()
    cam.read()
    assert len(pub.sent) == 1


def test_button_subscription(tmp_path):
    cam, pub = make_camera(tmp_path)
    assert "tower/camera/capture" in pub.subs


def test_daily_counter_resets(tmp_path):
    c = DailyCounter(StateStore(tmp_path / "s.json"))
    c.increment(date(2026, 11, 10))
    assert c.count(date(2026, 11, 10)) == 1
    assert c.count(date(2026, 11, 11)) == 0

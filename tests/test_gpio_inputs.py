from tower_monitor.sensors.gpio_inputs import DoorSensor, MainsSensor
from tower_monitor.state import StateStore


class FakePin:
    def __init__(self, low):
        self.is_active = low  # gpiozero with pull_up: active == pin LOW


def test_mains_present_when_low():
    m = MainsSensor({"gpio": 27})
    m._dev = FakePin(low=True)
    assert m.read() == {"mains": "ON"}
    m._dev = FakePin(low=False)
    assert m.read() == {"mains": "OFF"}


def test_mains_inverted():
    m = MainsSensor({"gpio": 27, "present_when_low": False})
    m._dev = FakePin(low=True)
    assert m.read() == {"mains": "OFF"}


def test_door_open_records_time_and_triggers_photo(tmp_path):
    d = DoorSensor({"gpio": 17})
    d.store = StateStore(tmp_path / "s.json")
    triggered, photos = [], []
    d.trigger = lambda: triggered.append(1)
    d.on_open = lambda: photos.append(1)

    d._dev = FakePin(low=True)       # closed
    d._was_open = False
    assert d.read()["door"] == "OFF"

    d._dev = FakePin(low=False)      # opened
    d._changed()
    out = d.read()
    assert out["door"] == "ON"
    assert out["last_opened"] is not None
    assert photos == [1] and triggered == [1]

    # bounce: a second "open" edge without closing doesn't re-trigger a photo
    d._changed()
    assert photos == [1]

    # survives restart
    assert StateStore(tmp_path / "s.json").get("door_last_opened") == out["last_opened"]

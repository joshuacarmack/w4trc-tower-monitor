"""Entry point: python -m tower_monitor [--config PATH] [--simulate] [-v]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import config as config_mod
from . import sensors as sensors_mod
from .mqtt import Publisher
from .runner import Runner


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tower_monitor")
    p.add_argument("-c", "--config", help=f"config file (default {config_mod.DEFAULT_PATH})")
    p.add_argument("--simulate", action="store_true",
                   help="fake all sensor readings; for bench testing without hardware")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",  # journald adds timestamps
        stream=sys.stdout,
    )

    cfg = config_mod.load(args.config)
    sensors = sensors_mod.build(cfg, simulate=args.simulate)
    runner = Runner(cfg, sensors, Publisher(cfg["mqtt"], cfg["device"]))

    def _stop(signum, frame):
        logging.info("Signal %s, shutting down", signum)
        runner.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

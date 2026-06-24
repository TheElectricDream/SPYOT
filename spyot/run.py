"""SPYOT software entry point - runs on each spacecraft computer.

Invoked as ``python -m spyot.run`` (this is exactly what the manager launches
on each remote host). It:

  1. loads a JSON run config (compiled by the manager from the user's YAML),
  2. builds the declared components in the configured mode,
  3. runs the precise fixed-rate loop, aligned to an absolute start epoch so
     that every instance ticks together,
  4. prints a timing summary on exit.

For the first iteration the default config contains a single ``heartbeat``
component, so running this just prints a synchronized message every tick.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict

from spyot.components import build_component
from spyot.core.scheduler import PreciseScheduler


def _load_runconfig(args: argparse.Namespace) -> Dict[str, Any]:
    if args.runconfig:
        return json.loads(Path(args.runconfig).read_text(encoding="utf-8"))
    if args.runconfig_json:
        return json.loads(args.runconfig_json)
    # Fall back to the project default config compiled on the fly.
    from spyot.config import load_config
    default = Path(__file__).resolve().parent.parent / "config" / "default.yaml"
    return load_config(default).to_runconfig()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPYOT software runner")
    parser.add_argument("--runconfig", help="path to a JSON run config")
    parser.add_argument("--runconfig-json", help="inline JSON run config string")
    parser.add_argument("--start-epoch", type=float, default=None,
                        help="absolute Unix time (coordinator clock) for tick 0")
    parser.add_argument("--clock-offset", type=float, default=0.0,
                        help="local_clock - coordinator_clock, seconds")
    parser.add_argument("--instance-name", default=socket.gethostname(),
                        help="label for this instance in the logs")
    args = parser.parse_args(argv)

    cfg = _load_runconfig(args)
    mode = cfg.get("mode", "simulation")
    rate = float(cfg["sample_rate_hz"])
    duration = cfg.get("duration_s")
    spin = float(cfg.get("spin_window_s", 0.0015))

    # Build components.
    specs = cfg.get("components") or [
        {"name": "heartbeat", "type": "heartbeat",
         "params": {"message": cfg.get("message", "SPYOT tick")}}
    ]
    components = [
        build_component(s["name"], s["type"], mode, s.get("params", {}))
        for s in specs
    ]

    print(f"[{args.instance_name}] SPYOT runner starting :: mode={mode} "
          f"rate={rate} Hz duration={duration}s components="
          f"{[c.name for c in components]}", flush=True)

    for c in components:
        c.setup()

    scheduler = PreciseScheduler(rate_hz=rate, spin_window_s=spin)

    # Graceful Ctrl-C / SIGTERM.
    def _handle(_sig, _frame):
        scheduler.stop()
    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (ValueError, AttributeError):  # not always available (e.g. Windows)
        pass

    # If no explicit start epoch was given (standalone run), start shortly in
    # the future so the loop begins deterministically.
    start_epoch = args.start_epoch
    if start_epoch is None:
        start_epoch = time.time() + 0.25
    wait = (start_epoch + args.clock_offset) - time.time()
    if wait > 0:
        print(f"[{args.instance_name}] waiting {wait:.3f}s for start barrier "
              f"(start_epoch={start_epoch:.6f}, offset={args.clock_offset:+.6f})",
              flush=True)

    def on_tick(index: int, sim_time: float, dt: float) -> None:
        command = {"_index": index}
        for c in components:
            c.step(sim_time, dt, command)

    try:
        stats = scheduler.run(
            on_tick,
            start_epoch=start_epoch,
            clock_offset=args.clock_offset,
            duration_s=duration,
        )
        print(f"[{args.instance_name}] done :: {stats.summary()}", flush=True)
    finally:
        for c in components:
            try:
                c.teardown()
            except Exception as exc:  # noqa: BLE001 - never mask shutdown
                print(f"[{args.instance_name}] teardown error in {c.name}: {exc}",
                      file=sys.stderr, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

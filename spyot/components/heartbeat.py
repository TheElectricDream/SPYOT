"""A trivial component used for the first iteration / smoke testing.

It does nothing physical: every tick it prints a message that includes the
instance identity and timing. When several synchronized instances run at once
you should see their messages line up tick-for-tick.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any, Dict

from spyot.core.component import Component


class Heartbeat(Component):
    """Prints a heartbeat message each tick.

    params:
        message    - text to print (default "SPYOT tick")
        every      - only print every Nth tick (default 1)
    """

    def setup(self) -> None:
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.message = self.params.get("message", "SPYOT tick")
        self.every = int(self.params.get("every", 1))
        self._t0_wall = time.time()

    def _beat(self, t: float, dt: float, index: int) -> Dict[str, Any]:
        if index % self.every == 0:
            wall = time.strftime("%H:%M:%S", time.localtime()) + \
                f".{int((time.time() % 1) * 1000):03d}"
            print(
                f"[{self.hostname}:{self.pid}] [{self.mode.value}] "
                f"tick={index:06d} t={t:8.3f}s wall={wall} :: {self.message}",
                flush=True,
            )
        return {"index": index, "t": t}

    # For this demo both modes behave identically; real components diverge here.
    def step_simulation(self, t: float, dt: float, command: Dict[str, Any]):
        return self._beat(t, dt, command.get("_index", int(round(t / dt))))

    def step_experiment(self, t: float, dt: float, command: Dict[str, Any]):
        return self._beat(t, dt, command.get("_index", int(round(t / dt))))

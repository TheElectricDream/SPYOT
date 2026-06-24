"""A precise, wall-clock-aligned fixed-rate scheduler.

Two requirements drive this design:

1. **Precision.** ``time.sleep`` alone is too coarse and drifts. We use a
   hybrid strategy: sleep for most of the interval, then busy-spin for the last
   ``spin_window_s`` to hit the target instant tightly.

2. **Cross-machine alignment.** For a synchronized experiment, every instance
   must tick at the *same wall-clock instants*. So ticks are aligned to an
   absolute epoch (``start_epoch``) expressed in the coordinator's clock, with a
   per-host ``clock_offset`` added to convert into local time. The scheduler
   never accumulates drift because every tick target is computed as
   ``start + i * dt`` rather than ``previous + dt``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


def precise_sleep_until(target_local: float, spin_window_s: float = 0.0015) -> None:
    """Block until ``time.time() >= target_local`` as precisely as practical.

    ``target_local`` is an absolute Unix timestamp in *this machine's* clock.
    """
    while True:
        remaining = target_local - time.time()
        if remaining <= 0:
            return
        if remaining > spin_window_s:
            # Leave a margin for the OS scheduler to wake us a bit late.
            time.sleep(remaining - spin_window_s)
        else:
            # Final approach: busy-wait for sub-millisecond accuracy.
            while time.time() < target_local:
                pass
            return


@dataclass
class LoopStats:
    """Timing statistics gathered while the loop runs (for jitter reporting)."""

    ticks: int = 0
    max_abs_jitter_s: float = 0.0
    sum_abs_jitter_s: float = 0.0
    max_overrun_s: float = 0.0  # how far a tick's *work* exceeded dt
    _jitter_samples: List[float] = field(default_factory=list, repr=False)

    def record(self, jitter_s: float, overrun_s: float) -> None:
        self.ticks += 1
        a = abs(jitter_s)
        self.max_abs_jitter_s = max(self.max_abs_jitter_s, a)
        self.sum_abs_jitter_s += a
        self.max_overrun_s = max(self.max_overrun_s, overrun_s)
        # Keep a bounded sample buffer to avoid unbounded growth on long runs.
        if len(self._jitter_samples) < 100_000:
            self._jitter_samples.append(jitter_s)

    @property
    def mean_abs_jitter_s(self) -> float:
        return self.sum_abs_jitter_s / self.ticks if self.ticks else 0.0

    def summary(self) -> str:
        return (
            f"ticks={self.ticks} "
            f"mean|jitter|={self.mean_abs_jitter_s * 1e3:.3f} ms "
            f"max|jitter|={self.max_abs_jitter_s * 1e3:.3f} ms "
            f"max_overrun={self.max_overrun_s * 1e3:.3f} ms"
        )


class PreciseScheduler:
    """Runs a callback at a fixed rate, aligned to an absolute start epoch."""

    def __init__(self, rate_hz: float, spin_window_s: float = 0.0015) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        self.rate_hz = rate_hz
        self.dt = 1.0 / rate_hz
        self.spin_window_s = spin_window_s
        self._stop = False

    def stop(self) -> None:
        """Request the loop to exit after the current tick."""
        self._stop = True

    def run(
        self,
        on_tick: Callable[[int, float, float], None],
        start_epoch: Optional[float] = None,
        clock_offset: float = 0.0,
        duration_s: Optional[float] = None,
    ) -> LoopStats:
        """Drive ``on_tick(index, sim_time, dt)`` at the configured rate.

        ``start_epoch``  - absolute Unix time (coordinator clock) of tick 0.
                           Defaults to "now"; the manager normally supplies a
                           future epoch so all instances share tick 0.
        ``clock_offset`` - ``local_clock - coordinator_clock`` (s). Added to
                           ``start_epoch`` to get the local target for tick 0.
        ``duration_s``   - stop after this many seconds (None = run forever).
        """
        if start_epoch is None:
            start_epoch = time.time()
        local_start = start_epoch + clock_offset

        stats = LoopStats()
        i = 0
        while not self._stop:
            sim_time = i * self.dt
            if duration_s is not None and sim_time >= duration_s:
                break

            target_local = local_start + sim_time
            precise_sleep_until(target_local, self.spin_window_s)

            actual = time.time()
            jitter = actual - target_local  # +ve = woke late

            on_tick(i, sim_time, self.dt)

            overrun = (time.time() - target_local) - self.dt
            stats.record(jitter, max(0.0, overrun))
            i += 1

        return stats

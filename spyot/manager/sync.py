"""Time synchronization and synchronized experiment launch (no ROS2).

Two problems to solve so that N instances tick together:

1. **Clock offset.** Machine clocks differ. The manager estimates each host's
   offset relative to its own clock using an NTP-style handshake: record local
   time t0, ask the remote for its time t_r, record local time t1; then
   ``offset ~= t_r - (t0 + t1) / 2`` and ``rtt = t1 - t0``. Repeating and keeping
   the sample with the smallest RTT rejects most network/scheduling noise.

   NOTE: the handshake here uses an SSH ``exec`` per sample, whose latency is
   dominated by remote process spawn. That is good to a few milliseconds, which
   is fine for the first iteration. For tighter sync, run chrony/NTP on the
   rigs (then offsets are ~0) - the rest of this machinery still applies.

2. **Coordinated start.** The manager picks an absolute ``start_epoch`` a few
   seconds in the future (its own clock). Each instance is launched with that
   epoch plus its own offset, and its scheduler busy-aligns tick 0 to
   ``start_epoch + offset`` in local time. All instances therefore fire tick 0
   at the same physical instant.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Dict, List, Optional

from spyot.config import Config, Host
from spyot.manager.ssh import SSHClient


# Tiny remote program that prints the remote Unix time with high precision.
_REMOTE_TIME_CMD = "{python} -c \"import time;print(repr(time.time()))\""


@dataclass
class OffsetEstimate:
    host: str
    offset_s: float      # remote_clock - manager_clock
    rtt_s: float         # best (smallest) round-trip observed
    samples: int


def measure_offset(ssh: SSHClient, host: Host, samples: int = 15) -> OffsetEstimate:
    best_rtt = float("inf")
    best_offset = 0.0
    n = 0
    cmd = _REMOTE_TIME_CMD.format(python=host.python)
    for _ in range(max(1, samples)):
        t0 = time.time()
        res = ssh.run(cmd, timeout=10)
        t1 = time.time()
        if not res.ok:
            continue
        try:
            t_remote = float(res.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            continue
        rtt = t1 - t0
        offset = t_remote - (t0 + t1) / 2.0
        n += 1
        if rtt < best_rtt:
            best_rtt = rtt
            best_offset = offset
    return OffsetEstimate(host.name, best_offset,
                          0.0 if best_rtt == float("inf") else best_rtt, n)


@dataclass
class LaunchHandle:
    host: str
    channel: object
    thread: threading.Thread


class ExperimentLauncher:
    """Measures offsets and launches synchronized runners across hosts."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def _remote_root(self, ssh: SSHClient) -> str:
        root = self.config.remote_root
        if root.startswith("~"):
            home = ssh.run("printf %s \"$HOME\"").stdout.strip() or "/root"
            root = root.replace("~", home, 1)
        return str(PurePosixPath(root))

    @staticmethod
    def _venv_python(remote_root: str) -> str:
        # Remotes are POSIX (the spot computers); venv interpreter lives in bin.
        return str(PurePosixPath(remote_root) / ".venv" / "bin" / "python")

    def run_experiment(
        self,
        hosts: List[Host],
        on_line: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, OffsetEstimate]:
        """Launch a synchronized experiment on ``hosts`` and stream their output.

        Blocks until every instance exits. ``on_line(host, line)`` is called for
        each line of remote output (defaults to printing).
        """
        if on_line is None:
            def on_line(host, line):  # noqa: E306
                print(f"[{host}] {line}", flush=True)

        # 1. Connect to all hosts and measure clock offsets.
        conns: Dict[str, SSHClient] = {}
        roots: Dict[str, str] = {}
        offsets: Dict[str, OffsetEstimate] = {}
        try:
            for h in hosts:
                ssh = SSHClient(h.ip, h.user, h.password)
                ssh.connect()
                conns[h.name] = ssh
                roots[h.name] = self._remote_root(ssh)
                est = measure_offset(ssh, h, self.config.sync.offset_samples)
                offsets[h.name] = est
                print(f"[sync] {h.name}: offset={est.offset_s * 1e3:+.3f} ms "
                      f"rtt={est.rtt_s * 1e3:.3f} ms ({est.samples} samples)",
                      flush=True)

            # 2. Choose a common start epoch in the manager's clock.
            start_epoch = time.time() + self.config.sync.lead_time_s
            print(f"[sync] start_epoch={start_epoch:.6f} "
                  f"(in {self.config.sync.lead_time_s:.1f} s)", flush=True)

            # 3. Launch each runner with its offset and the shared start epoch.
            handles: List[LaunchHandle] = []
            for h in hosts:
                ssh = conns[h.name]
                root = roots[h.name]
                py = self._venv_python(root)
                offset = offsets[h.name].offset_s
                runconfig = str(PurePosixPath(root) / "runconfig.json")
                cmd = (
                    f"cd {root} && {py} -m spyot.run "
                    f"--runconfig {runconfig} "
                    f"--start-epoch {start_epoch:.6f} "
                    f"--clock-offset {offset:.9f} "
                    f"--instance-name {h.name}"
                )
                channel = ssh.start(cmd)
                t = threading.Thread(
                    target=self._pump, args=(h.name, channel, on_line), daemon=True)
                t.start()
                handles.append(LaunchHandle(h.name, channel, t))

            # 4. Wait for all instances to finish.
            for hnd in handles:
                hnd.thread.join()
            return offsets
        finally:
            for ssh in conns.values():
                ssh.close()

    @staticmethod
    def _pump(host: str, channel, on_line: Callable[[str, str], None]) -> None:
        buf = b""
        while True:
            if channel.recv_ready():
                data = channel.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    on_line(host, line.decode("utf-8", "replace").rstrip("\r"))
            elif channel.exit_status_ready() and not channel.recv_ready():
                break
            else:
                time.sleep(0.01)
        if buf:
            on_line(host, buf.decode("utf-8", "replace").rstrip("\r"))

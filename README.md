# SPYOT

**S**imulation and **P**h**Y**sical **O**perations **T**estbed — a small,
flexible Python framework for running **simulations** and **hardware
experiments** of planar (3-DOF: x, y, yaw) spacecraft.

This first iteration ships the two foundations you asked for:

1. **A manager** — cross-platform (Windows/macOS/Linux) controller that handles
   user configuration, creates the right Python virtual environment, deploys
   the software + environment to remote spacecraft computers, and launches
   **time-synchronized** experiments across them (no ROS2).
2. **The software** — a precise fixed-rate loop that steps pluggable
   `Component`s. Out of the box it runs a `heartbeat` component that prints a
   synchronized message every tick, so you can verify timing and sync end to
   end. A fully worked `thrusters` component is included as the template for
   adding real subsystems.

> **New here?** Read the [User Guide](docs/USER_GUIDE.md) — it documents every
> config option and CLI command, and walks through the hardware interface end
> to end.

---

## Layout

```
SPYOT/
├── manager.py                 # manager entry point (python manager.py ...)
├── config/default.yaml        # user-editable configuration
├── requirements.txt           # software deps (numpy) — installed on every host
├── requirements-manager.txt   # manager deps (pyyaml, paramiko) — controller only
└── spyot/
    ├── config.py              # config model + YAML/JSON loading
    ├── run.py                 # software entry point (python -m spyot.run)
    ├── core/
    │   ├── component.py        # Component ABC: simulation + experiment modes
    │   └── scheduler.py        # precise, wall-clock-aligned fixed-rate loop
    ├── components/
    │   ├── heartbeat.py        # first-iteration demo component
    │   └── thrusters.py        # worked example / template for a subsystem
    └── manager/
        ├── cli.py              # the `init/env/run/offsets/deploy/experiment` CLI
        ├── environment.py      # cross-platform venv check/create
        ├── deployment.py       # upload source + rebuild remote venv
        ├── sync.py             # clock-offset estimation + synchronized launch
        ├── ssh.py              # paramiko SSH/SFTP helper
        └── bootstrap.py        # auto-install manager deps on demand
```

---

## Quick start (local — no remote machines needed)

Everything local uses only the Python standard library.

```bash
# 1. Check / create the project virtual environment (Python version from config)
python manager.py env

# 2. Run the software locally — prints a synchronized heartbeat at 10 Hz for 10 s
python manager.py run

# Inspect the resolved configuration at any time
python manager.py config
```

Change the rate, duration, and message in `config/default.yaml`
(`sample_rate_hz`, `duration_s`, `message`) and re-run. The runner prints a
timing summary on exit, e.g.:

```
[local] done :: ticks=100 mean|jitter|=0.082 ms max|jitter|=0.310 ms max_overrun=0.000 ms
```

> The default `python_version` is `3.11`. If that interpreter isn't installed,
> either install it or change `python_version` in `config/default.yaml` to a
> version you have (the env manager warns and falls back to the best match).

---

## Remote deployment & synchronized experiments

Remote operations need `paramiko` (and YAML configs need `pyyaml`). Install the
manager deps once, or pass `--auto-install` to fetch them automatically:

```bash
pip install -r requirements-manager.txt
```

The default config defines three hosts — `spot-red` (192.168.1.110),
`spot-black` (192.168.1.111), `spot-blue` (192.168.1.112). **Set the SSH
`user` for each host in `config/default.yaml`** — the login name was not
specified, so `spot` is a placeholder.

```bash
# Measure clock offsets to the rigs (sanity check before syncing)
python manager.py offsets

# Deploy source + (re)build the venv on every host
python manager.py deploy

# Deploy to a subset only
python manager.py deploy --hosts spot-red,spot-blue

# Deploy (if needed) and launch a synchronized run across the selected hosts.
# Output from every instance is streamed back, prefixed by host name.
python manager.py experiment --hosts spot-red,spot-blue
```

### How synchronization works (no ROS2)

* **Clock offset.** For each host the manager runs an NTP-style handshake
  (record local t0 → ask remote for its time → record local t1; offset ≈
  `t_remote − (t0+t1)/2`, keep the smallest-round-trip sample). Good to a few
  milliseconds over SSH; for tighter sync run chrony/NTP on the rigs so offsets
  are ≈ 0 — the rest of the machinery is unchanged.
* **Coordinated start.** The manager picks an absolute `start_epoch` a few
  seconds in the future and launches each instance with that epoch plus its own
  offset. Each scheduler busy-aligns tick 0 to `start_epoch + offset` in local
  time, so all instances fire tick 0 at the same physical instant and never
  accumulate drift (every tick target is `start + i·dt`).

### Why the environment is *rebuilt*, not copied

Virtualenvs embed absolute paths and platform-specific binaries, so they aren't
portable. "Deploying the environment" therefore means: upload the source, then
run the **same** env logic on the target (`python manager.py env`) to build a
matching venv there. One code path, identical results on the controller and on
every rig.

---

## Adding a new subsystem (the template)

Every part of the spacecraft is a `Component` with two behaviors:

```python
from spyot.core.component import Component

class MyThing(Component):
    def setup(self): ...          # open ports / allocate state
    def step_simulation(self, t, dt, command): ...   # model it in software
    def step_experiment(self, t, dt, command): ...   # talk to real hardware
    def teardown(self): ...       # release resources
```

Register it in `spyot/components/__init__.py` and reference its `type` from the
`components:` list in your config.

`spyot/components/thrusters.py` is the worked reference. It implements your
exact example:

* **experiment:** desired wrench `[Fx, Fy, Mz]` → duty cycles (via the
  pseudo-inverse of the thrust distribution matrix) → serial frame to a Teensy
  running hardware PWM (`TeensyInterface`, stubbed pending the firmware
  protocol).
* **simulation:** desired wrench → duty cycles → first-order solenoid
  open/close actuation model (thrust decay + valve timing) → realized,
  saturated wrench `w = B·f` → rigid-body dynamics (`F = ma`, `M = Iα`).

---

## Notes & assumptions for this iteration

- SSH **username** is unspecified; set `hosts[].user` before deploying.
- The `TeensyInterface` and the experiment-mode serial path are stubs (they
  print instead of writing to a port) until the firmware protocol is fixed.
- Remote hosts are assumed POSIX (the venv interpreter is `…/.venv/bin/python`).
- For sub-millisecond cross-machine sync, run chrony/NTP on the rigs.

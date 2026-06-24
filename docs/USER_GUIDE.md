# SPYOT User Guide

This guide explains **everything you can configure and run** in SPYOT, then
walks through a complete **hardware-interface example** end to end. It is meant
to be read top to bottom the first time, and used as a reference afterward.

Contents:

1. [Mental model](#1-mental-model)
2. [Installation & first run](#2-installation--first-run)
3. [The configuration file — every option](#3-the-configuration-file--every-option)
4. [The manager CLI — every command](#4-the-manager-cli--every-command)
5. [The software runner — every option](#5-the-software-runner--every-option)
6. [Components — the building blocks](#6-components--the-building-blocks)
7. [Walkthrough: the thruster hardware interface](#7-walkthrough-the-thruster-hardware-interface)
8. [Walkthrough: building your own hardware interface from scratch](#8-walkthrough-building-your-own-hardware-interface-from-scratch)
9. [Running on the real rigs (deploy + sync)](#9-running-on-the-real-rigs-deploy--sync)
10. [Quick reference](#10-quick-reference)

---

## 1. Mental model

SPYOT has two halves:

| Half | Lives in | Runs on | Job |
|------|----------|---------|-----|
| **Manager** | `spyot/manager/`, `manager.py` | your laptop (the *controller*) | configure, build environments, deploy, synchronize, launch |
| **Software** | `spyot/core/`, `spyot/components/`, `spyot/run.py` | each spacecraft computer (and your laptop for local tests) | run a precise fixed-rate loop that steps the spacecraft's **components** |

A **component** is one part of the spacecraft (thrusters, a reaction wheel, a
metrology feed, the rigid-body dynamics, ...). Every component knows how to do
two things:

- **`simulation`** — model itself in software.
- **`experiment`** — talk to real hardware.

You pick which one with a single config switch (`mode`). The loop, the timing,
the deployment, and the synchronization are all **identical** in both modes —
only the components behave differently. That is the whole design.

```
              ┌──────────────── manager (your laptop) ────────────────┐
              │  config.yaml → build venv → deploy → measure offsets   │
              │             → pick start_epoch → launch                │
              └───────────────────────────┬───────────────────────────┘
                          ssh/sftp         │   (no ROS2)
        ┌───────────────────┬──────────────┴───────────────┐
        ▼                   ▼                               ▼
   spot-red             spot-black                      spot-blue
   spyot.run            spyot.run                       spyot.run
   ┌─ precise loop ─┐   ┌─ precise loop ─┐              ┌─ precise loop ─┐
   │ tick → step()  │   │ tick → step()  │   ...all aligned to the same  │
   │ components     │   │ components     │      absolute start instant   │
   └────────────────┘   └────────────────┘              └────────────────┘
```

---

## 2. Installation & first run

### Requirements

- **Python** on your laptop. The config asks for a specific version
  (`python_version`, default `"3.11"`); see [§3](#environment-options). If you
  don't have 3.11, either install it or set `python_version` to a version you
  have.
- For **local** simulation testing, nothing else is needed — the manager's
  local commands use only the Python standard library.
- For **remote** deployment/sync, install the manager dependencies:
  ```bash
  pip install -r requirements-manager.txt   # pyyaml + paramiko
  ```
  (or pass `--auto-install` to any remote command and SPYOT installs them for you.)

### First run (local, no hardware)

```bash
# 1. Create the project virtual environment (reads python_version from config)
python manager.py env

# 2. Run the software locally for 10 s at 10 Hz, printing a heartbeat each tick
python manager.py run
```

You should see synchronized heartbeat lines and a timing summary like:

```
[local] done :: ticks=100 mean|jitter|=0.05 ms max|jitter|=0.3 ms max_overrun=0.000 ms
```

That confirms the loop, the timing, and the component system all work. Now
let's see what you can change.

---

## 3. The configuration file — every option

Everything is driven by a single YAML file: **`config/default.yaml`**. You can
keep multiple configs and choose one with the global `--config` flag (see
[§4](#4-the-manager-cli--every-command)).

> **Format:** YAML is used for human editing. Internally the manager compiles
> the run-relevant parts to a small JSON "run config" that the software reads —
> so the remote machines never need a YAML library. You normally never touch the
> JSON; it's generated for you.

Below is every field, its default, and what it does.

### Environment options

| Field | Default | What it controls |
|-------|---------|------------------|
| `python_version` | `"3.11"` | The Python **major.minor** the virtual environment must use. The manager looks for a matching interpreter (`python3.11`, or `py -3.11` on Windows). If it can't find an exact match it warns and falls back to the best available. |
| `venv_dir` | `".venv"` | Where the virtual environment is created, relative to the project root. |
| `requirements` | `"requirements.txt"` | The pip requirements file installed into the venv. Add your software's Python dependencies here. |

> **Override the interpreter directly:** set the environment variable
> `SPYOT_PYTHON=/path/to/python` to force a specific interpreter, ignoring
> `python_version` discovery.

### Execution options

| Field | Default | What it controls |
|-------|---------|------------------|
| `mode` | `"simulation"` | `"simulation"` or `"experiment"`. This is the master switch that decides whether components model themselves or drive real hardware. |
| `sample_rate_hz` | `10.0` | The loop rate in Hz. This is the precise tick rate. Try `100.0`, `500.0`, etc. |
| `duration_s` | `10.0` | How long to run, in seconds. Set to `null` to run **forever** (until Ctrl-C). |
| `message` | `"SPYOT tick"` | Default text used by the heartbeat demo component. |

### Components

`components` is a **list**. Each entry builds one component:

```yaml
components:
  - name: heartbeat        # a label you choose (must be unique-ish)
    type: heartbeat        # which component class to build (see the registry)
    params:                # passed straight to that component
      message: "hello from SPYOT"
      every: 1
```

- `name` — your label for this instance, shown in logs.
- `type` — the registered component type. Built-in types: **`heartbeat`**,
  **`thrusters`**. You add your own (see [§8](#8-walkthrough-building-your-own-hardware-interface-from-scratch)).
- `params` — a free-form dictionary handed to the component. Each component
  documents its own params (see [§6](#6-components--the-building-blocks)).

You can list **as many components as you want**; the loop steps them in order
every tick.

### Deployment options

| Field | Default | What it controls |
|-------|---------|------------------|
| `remote_root` | `"~/spyot"` | The directory on each remote machine where SPYOT is uploaded and run. `~` is expanded to the remote user's home. |

### Synchronization options

| Field | Default | What it controls |
|-------|---------|------------------|
| `sync.lead_time_s` | `5.0` | How many seconds in the future the shared start instant is scheduled, giving every machine time to be ready before tick 0. |
| `sync.offset_samples` | `15` | How many clock-offset handshakes are taken per host. More samples = better offset estimate, slightly slower startup. |
| `sync.spin_window_s` | `0.0015` | The busy-wait window (seconds) at the end of each tick's sleep. Larger = more CPU but tighter timing; smaller = less CPU, looser timing. `0.0015` (1.5 ms) is a good default. |

### Hosts

`hosts` is the list of remote spacecraft computers:

```yaml
hosts:
  - name: spot-red          # friendly name used with --hosts
    ip: "192.168.1.110"     # IP or hostname on the LAN
    user: "spot"            # SSH login name  ←  SET THIS to the real account
    password: "srcl2023"    # SSH password (or omit to use SSH keys/agent)
    python: "python3"       # the interpreter to use on the remote
```

| Field | Default | What it controls |
|-------|---------|------------------|
| `name` | — | Friendly name; what you pass to `--hosts`. |
| `ip` | — | Address on the network. |
| `user` | `"spot"` | SSH username. **The real login was not provided — set this.** |
| `password` | none | SSH password. If omitted, SPYOT uses your SSH agent / key files instead. |
| `python` | `"python3"` | Interpreter used on the remote to build the venv and run. |

> **Security note:** storing a password in the YAML is convenient for a lab rig
> but not great hygiene. You can instead leave `password` out and use SSH keys
> (`ssh-copy-id spot@192.168.1.110`), which SPYOT will use automatically.

---

## 4. The manager CLI — every command

All commands are `python manager.py <command> [options]`. Two **global** flags
go *before* the command:

| Global flag | Purpose |
|-------------|---------|
| `--config PATH` | Use a different config file (default `config/default.yaml`). |
| `--auto-install` | Auto-install missing manager deps (`pyyaml`, `paramiko`) if needed. |

Example: `python manager.py --config experiments/run42.yaml run`

### `init` — create a starter config

```bash
python manager.py --config my.yaml init          # writes a copy of the default config
python manager.py --config my.yaml init --force  # overwrite if it exists
```

### `config` — show the resolved configuration

```bash
python manager.py config
```
Prints the compiled JSON run config plus the host list. Use it to sanity-check
what SPYOT actually parsed.

### `env` — check / create the local virtual environment

```bash
python manager.py env            # create the venv if missing/mismatched, install requirements
python manager.py env --check    # only report status; exit code 0 if OK, 1 if not
python manager.py env --force    # delete and rebuild from scratch
```

### `run` — run the software locally

```bash
python manager.py run                          # uses the project venv
python manager.py run --instance-name bench1   # label the logs
python manager.py run --system-python          # use the current interpreter, not the venv
```
This runs a single local instance (no networking) using `mode`,
`sample_rate_hz`, `duration_s`, and `components` from the config. Great for
developing and testing simulation components.

### `offsets` — measure clock offsets to the rigs

```bash
python manager.py offsets                       # all hosts
python manager.py offsets --hosts spot-red,spot-blue
```
Prints each host's estimated clock offset and round-trip time. Run it before a
synchronized experiment to confirm the network and clocks look sane.

### `deploy` — push source + build the remote environment

```bash
python manager.py deploy                         # all hosts
python manager.py deploy --hosts spot-red        # one host
python manager.py deploy --no-env                # upload source only, skip venv rebuild
```
Uploads the project to `remote_root` on each host and rebuilds the venv there
(because virtualenvs aren't portable — same env logic runs on the target).

### `experiment` — deploy + launch a synchronized run

```bash
python manager.py experiment                              # deploy to all, then run synchronized
python manager.py experiment --hosts spot-red,spot-blue   # subset
python manager.py experiment --no-deploy                  # skip deploy; hosts already up to date
python manager.py experiment --no-env                     # deploy code but don't rebuild venvs
```
This is the headline command: it (optionally) deploys, measures clock offsets,
picks a shared start instant, launches `spyot.run` on every selected host
aligned to that instant, and streams all their output back to your terminal,
prefixed by host name.

---

## 5. The software runner — every option

You normally launch the runner indirectly (via `manager.py run` or
`experiment`), but you can call it directly for debugging:

```bash
python -m spyot.run [options]
```

| Option | Purpose |
|--------|---------|
| `--runconfig PATH` | Read a JSON run config from a file. |
| `--runconfig-json '<json>'` | Pass the JSON run config inline (handy for one-off tests). |
| `--start-epoch FLOAT` | Absolute Unix time (coordinator clock) for tick 0. Omit to start ~0.25 s from now. |
| `--clock-offset FLOAT` | `local_clock − coordinator_clock`, in seconds. The manager fills this in per host. |
| `--instance-name NAME` | Label for this instance in the logs (default: hostname). |

Example — a 2-second, 100 Hz simulation of just a heartbeat:

```bash
python -m spyot.run --runconfig-json \
  '{"mode":"simulation","sample_rate_hz":100.0,"duration_s":2.0,
    "components":[{"name":"hb","type":"heartbeat","params":{"every":25}}]}'
```

---

## 6. Components — the building blocks

A component is a Python class registered under a `type` name. Built-ins:

### `heartbeat`

A do-nothing demo that prints a message each tick. Useful for verifying timing
and synchronization.

| Param | Default | Meaning |
|-------|---------|---------|
| `message` | `"SPYOT tick"` | Text to print. |
| `every` | `1` | Print only every Nth tick (e.g. `100` to thin output at high rates). |

### `thrusters` (`PlanarThrusterSystem`)

The worked hardware example — solenoid thrusters for a planar spacecraft. See
the [full walkthrough in §7](#7-walkthrough-the-thruster-hardware-interface).

| Param | Default | Meaning |
|-------|---------|---------|
| `distribution` | built-in 3×8 | The 3×N thrust distribution matrix `B` (rows = Fx, Fy, Mz). |
| `max_thrust` | `0.25` | Max force per solenoid, in newtons. |
| `min_duty` | `0.05` | Duty cycles below this are squelched to 0 (avoids valve dribble). |
| `tau_open_s` | `0.010` | Solenoid open time constant (simulation). |
| `tau_close_s` | `0.015` | Solenoid close time constant (simulation). |
| `mass` | `12.0` | Spacecraft mass in kg (simulation dynamics). |
| `inertia` | `0.20` | Yaw inertia Izz in kg·m² (simulation dynamics). |
| `teensy_port` | `"/dev/ttyACM0"` | Serial port of the PWM Teensy (experiment mode). |

---

## 7. Walkthrough: the thruster hardware interface

This is the system from the project brief, implemented in
`spyot/components/thrusters.py`. It shows exactly how one component supports
both modes.

### The physics, briefly

A planar spacecraft has a 3-element body **wrench** `w = [Fx, Fy, Mz]`. Each of
the `N` solenoid thrusters produces a non-negative force; the **thrust
distribution matrix** `B` (shape `3 × N`) maps per-thruster forces `f` into the
body wrench:

```
w = B · f          # f is the vector of thruster forces, each in [0, max_thrust]
```

To find the duty cycles that achieve a desired wrench, we invert that with the
pseudo-inverse and normalize by the max thrust:

```
f_des = pinv(B) · w_des
duty  = clip(f_des / max_thrust, 0, 1)
```

### Experiment mode (real hardware)

```
controller wrench [Fx,Fy,Mz]
        │
        ▼  pinv(B), clip, squelch
   duty cycles  ──►  TeensyInterface.send_duties()  ──►  Teensy (hardware PWM)  ──►  solenoids fire
```

In code (`step_experiment`): the desired wrench → duty cycles → a serial frame
sent to the Teensy. The Teensy runs its own firmware that turns those duty
cycles into hardware PWM driving the valves.

The serial side is a **stub** right now (`TeensyInterface` prints instead of
writing bytes) so the whole pipeline runs without hardware attached. To make it
real, you fill in three methods in `spyot/components/thrusters.py`:

```python
class TeensyInterface:
    def open(self):
        import serial                                  # pyserial
        self._serial = serial.Serial(self.port, self.baud, timeout=0.01)

    def send_duties(self, duties):
        # Example frame: 'D' + one byte (0..255) per thruster + newline.
        frame = b"D" + bytes(int(d * 255) for d in duties) + b"\n"
        self._serial.write(frame)

    def close(self):
        if self._serial:
            self._serial.close()
```

Then add `pyserial` to `requirements.txt`, set `teensy_port` to your device
(e.g. `/dev/ttyACM0`), and flip the config to experiment mode. (The exact frame
format must match whatever your Teensy firmware expects — `'D' + bytes + '\n'`
above is just an illustration.)

### Simulation mode (no hardware)

```
controller wrench [Fx,Fy,Mz]
        │
        ▼  pinv(B), clip, squelch
   duty cycles
        │
        ▼  first-order solenoid open/close model (tau_open, tau_close)  ← thrust decay & valve timing
   realized per-thruster force f_real (lagged, saturated)
        │
        ▼  w_real = B · f_real                                          ← realized, saturated wrench
   body wrench  ──►  rotate into inertial frame  ──►  F = ma, M = Iα   ← rigid-body dynamics
        │
        ▼
   updated pose [x, y, θ] and twist [vx, vy, ω]
```

In code (`step_simulation`): the same duty cycles are passed through
`_actuation_model()`, which lags each thruster's force toward its commanded duty
using `tau_open_s` / `tau_close_s` (capturing finite valve timing and thrust
decay). The realized forces are recombined into a **saturated** wrench
`w = B·f_real`, rotated into the inertial frame, and integrated with simple
rigid-body dynamics. The component returns the pose and twist as telemetry.

### Trying it now (simulation)

Edit `config/default.yaml` so `components` includes the thruster (the file ships
with it commented out):

```yaml
mode: "simulation"
sample_rate_hz: 100.0
duration_s: 5.0
components:
  - name: thrusters
    type: thrusters
    params:
      max_thrust: 0.25
      mass: 12.0
      inertia: 0.20
```

Then:

```bash
python manager.py env     # ensure numpy is installed
python manager.py run
```

> Note: with the default loop the controller command is zero wrench, so the
> thrusters stay idle. Feeding a real command into the loop (a controller /
> trajectory) is the next feature we'll build — see [§8](#wiring-inputs-into-the-loop)
> for where the command comes from.

### Switching the same component to hardware

1. Fill in `TeensyInterface` (above) and add `pyserial` to `requirements.txt`.
2. Set `teensy_port` in the component params.
3. Change `mode: "experiment"` (locally) **or** deploy with the host's config in
   experiment mode and run `python manager.py experiment`.

Nothing else changes — same loop, same timing, same sync.

---

## 8. Walkthrough: building your own hardware interface from scratch

Say you want to add a **reaction wheel**. Here is the complete recipe; it is the
same for any subsystem (metrology camera, IMU, magnetorquer, ...).

### Step 1 — copy the template

Create `spyot/components/reaction_wheel.py`:

```python
from spyot.core.component import Component


class ReactionWheel(Component):
    """A single reaction wheel producing yaw torque."""

    # --- runs once before the loop -------------------------------------
    def setup(self):
        self.max_torque = float(self.params.get("max_torque", 0.05))  # N·m
        self.wheel_speed = 0.0                                         # rad/s (sim state)
        if self.mode.value == "experiment":
            # Open your real interface here (serial, CAN, GPIO, ...).
            # self.bus = open_motor_bus(self.params["port"])
            print("[wheel] (stub) opened motor bus")

    # --- runs once after the loop (always) -----------------------------
    def teardown(self):
        if self.mode.value == "experiment":
            # self.bus.close()
            print("[wheel] (stub) closed motor bus")

    # --- runs every tick in simulation ---------------------------------
    def step_simulation(self, t, dt, command):
        tau_cmd = max(-self.max_torque, min(self.max_torque,
                                            command.get("wheel_torque", 0.0)))
        # toy wheel model: speed integrates commanded torque
        self.wheel_speed += tau_cmd * dt
        return {"wheel_torque": tau_cmd, "wheel_speed": self.wheel_speed}

    # --- runs every tick in experiment ---------------------------------
    def step_experiment(self, t, dt, command):
        tau_cmd = max(-self.max_torque, min(self.max_torque,
                                            command.get("wheel_torque", 0.0)))
        # self.bus.set_torque(tau_cmd)
        return {"wheel_torque": tau_cmd}
```

The four lifecycle methods are the whole contract:

| Method | When | Use it for |
|--------|------|-----------|
| `setup()` | once, before the loop | open ports, allocate state |
| `step_simulation(t, dt, command)` | every tick (sim mode) | model the subsystem in software |
| `step_experiment(t, dt, command)` | every tick (experiment mode) | drive real hardware |
| `teardown()` | once, after the loop (always) | close ports, safe the hardware |

`t` is seconds since start, `dt` is the timestep, `command` is a dict of inputs.
Whatever dict you **return** becomes that component's telemetry for the tick.

### Step 2 — register it

In `spyot/components/__init__.py`:

```python
from spyot.components.reaction_wheel import ReactionWheel

REGISTRY = {
    "heartbeat": Heartbeat,
    "thrusters": PlanarThrusterSystem,
    "reaction_wheel": ReactionWheel,   # ← add this line
}
```

### Step 3 — use it in the config

```yaml
components:
  - name: rw1
    type: reaction_wheel
    params:
      max_torque: 0.05
      port: "/dev/ttyACM1"   # only used in experiment mode
```

### Step 4 — run it

```bash
python manager.py run        # simulation
# ...later, with hardware and mode: experiment...
python manager.py experiment
```

That's it — your new subsystem now participates in the precise loop, on your
laptop and on every rig, in both modes.

### Wiring inputs into the loop

Right now the loop passes each component a minimal `command` (`{"_index": i}`),
so components that need real inputs (a desired wrench, a wheel torque) see
zeros. The **command source** — a controller, a trajectory player, or
inter-component wiring — is the next thing to build. The component API above
already accepts it: a component just reads `command["wrench"]` /
`command["wheel_torque"]`, and one component's returned telemetry can feed
another's command. When we add the controller, no component code has to change.

---

## 9. Running on the real rigs (deploy + sync)

Once your components work in simulation, moving to hardware is mechanical:

1. **Set the SSH `user`** for each host in `config/default.yaml` (the real login
   account). Optionally switch to SSH keys instead of the password.
2. **Install manager deps** on your laptop: `pip install -r requirements-manager.txt`.
3. **Check connectivity & clocks:**
   ```bash
   python manager.py offsets
   ```
4. **Deploy** (uploads code, rebuilds each remote venv):
   ```bash
   python manager.py deploy
   ```
5. **Run a synchronized experiment** on the machines you want:
   ```bash
   python manager.py experiment --hosts spot-red,spot-blue
   ```
   Every selected instance starts at the same physical instant and ticks
   together; their output streams back to your terminal.

> **Tighter sync:** the offset handshake over SSH is good to a few
> milliseconds. For sub-millisecond alignment, run chrony/NTP on the rigs so the
> clocks already agree (offsets ≈ 0); everything else stays the same.

---

## 10. Quick reference

```bash
# Configuration
python manager.py --config FILE init [--force]   # make a starter config
python manager.py config                         # show resolved config

# Environment (local)
python manager.py env [--check] [--force]        # check / create / rebuild venv

# Run (local)
python manager.py run [--instance-name N] [--system-python]

# Remote (need paramiko: pip install -r requirements-manager.txt, or --auto-install)
python manager.py offsets    [--hosts a,b]
python manager.py deploy     [--hosts a,b] [--no-env]
python manager.py experiment [--hosts a,b] [--no-deploy] [--no-env]
```

**The three things you change most often** (in `config/default.yaml`):

- `sample_rate_hz` — how fast the loop ticks.
- `mode` — `simulation` vs `experiment`.
- `components` — what the spacecraft is made of.

**To add hardware:** write a `Component` subclass with
`setup/step_simulation/step_experiment/teardown`, register it in
`spyot/components/__init__.py`, and reference its `type` in the config. See
[§8](#8-walkthrough-building-your-own-hardware-interface-from-scratch).

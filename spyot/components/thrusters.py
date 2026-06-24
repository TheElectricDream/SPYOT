"""Planar thruster system - the worked example / template for a subsystem.

This component demonstrates the two-mode design described in the project brief.

Geometry / linear algebra
--------------------------
A planar spacecraft has a 3-element body wrench ``w = [Fx, Fy, Mz]``. The
solenoid thrusters each produce a (non-negative) force; thruster ``j`` maps
into the body wrench through column ``j`` of the *thrust distribution matrix*
``B`` (shape ``3 x N``)::

    w = B @ f                  # f: per-thruster force vector (N,), each in [0, Fmax]

To find duty cycles that achieve a desired wrench ``w_des`` we invert that
relationship with the pseudo-inverse and normalize by the max thrust::

    f_des = pinv(B) @ w_des
    duty  = clip(f_des / Fmax, 0, 1)

Experiment mode
---------------
``w_des -> duty -> Teensy``. The duty cycles are packed into a serial frame and
sent to a Teensy running a hardware-PWM firmware, which drives the solenoids.

Simulation mode
---------------
``w_des -> duty -> solenoid actuation model -> realized wrench -> (F = ma)``.
The actuation model captures finite valve open/close timing and thrust decay,
so the *realized* per-thruster force lags and is saturated relative to the
commanded duty. The realized, saturated wrench is then ``w_real = B @ f_real``
and is integrated with simple rigid-body dynamics.

``numpy`` is imported lazily so the rest of SPYOT (e.g. the heartbeat demo)
works even before the software venv is built.
"""

from __future__ import annotations

from typing import Any, Dict, List

from spyot.core.component import Component


# --------------------------------------------------------------------------- #
# Hardware interface stub
# --------------------------------------------------------------------------- #

class TeensyInterface:
    """Thin serial wrapper for the PWM Teensy. Stubbed for the first iteration.

    Replace the body of ``open``/``send_duties``/``close`` with real
    ``pyserial`` calls once the firmware protocol is fixed.
    """

    def __init__(self, port: str, baud: int = 115200) -> None:
        self.port = port
        self.baud = baud
        self._serial = None

    def open(self) -> None:
        # import serial
        # self._serial = serial.Serial(self.port, self.baud, timeout=0.01)
        print(f"[TeensyInterface] (stub) open {self.port} @ {self.baud} baud",
              flush=True)

    def send_duties(self, duties: List[float]) -> None:
        # Real implementation: pack duties into the firmware frame and write it.
        # frame = b"D" + bytes(int(d * 255) for d in duties) + b"\n"
        # self._serial.write(frame)
        pass

    def close(self) -> None:
        # if self._serial: self._serial.close()
        print(f"[TeensyInterface] (stub) close {self.port}", flush=True)


# --------------------------------------------------------------------------- #
# Default planar geometry: 8 thrusters (a pair on each of 4 faces), pure x/y.
# --------------------------------------------------------------------------- #

def _default_distribution() -> List[List[float]]:
    """Return a 3xN distribution matrix B for a symmetric 8-thruster layout.

    Columns: +x, -x (force in x), +y, -y (force in y), and four couple
    thrusters producing +/-Mz. Values are illustrative; override via params.
    """
    return [
        # Fx row
        [1, -1, 0, 0, 0, 0, 0, 0],
        # Fy row
        [0, 0, 1, -1, 0, 0, 0, 0],
        # Mz row (couples)
        [0, 0, 0, 0, 1, -1, 1, -1],
    ]


class PlanarThrusterSystem(Component):
    """Solenoid thruster bank for a planar spacecraft.

    params:
        distribution   - 3xN list-of-lists thrust distribution matrix B
        max_thrust     - per-thruster max force, N (default 0.25)
        min_duty       - duties below this are squelched to 0 (default 0.05)
        tau_open_s     - solenoid open time constant, s (default 0.010)
        tau_close_s    - solenoid close time constant, s (default 0.015)
        mass           - spacecraft mass, kg (sim only, default 12.0)
        inertia        - yaw inertia Izz, kg m^2 (sim only, default 0.20)
        teensy_port    - serial port for experiment mode (default "/dev/ttyACM0")
    """

    # -- lifecycle ---------------------------------------------------------- #

    def setup(self) -> None:
        import numpy as np  # lazy
        self.np = np

        self.B = np.asarray(
            self.params.get("distribution", _default_distribution()), dtype=float
        )
        if self.B.shape[0] != 3:
            raise ValueError("distribution matrix B must have 3 rows [Fx, Fy, Mz]")
        self.n_thrusters = self.B.shape[1]
        self.B_pinv = np.linalg.pinv(self.B)

        self.max_thrust = float(self.params.get("max_thrust", 0.25))
        self.min_duty = float(self.params.get("min_duty", 0.05))
        self.tau_open = float(self.params.get("tau_open_s", 0.010))
        self.tau_close = float(self.params.get("tau_close_s", 0.015))

        # Simulation-only rigid-body state: [x, y, theta, vx, vy, omega]
        self.mass = float(self.params.get("mass", 12.0))
        self.inertia = float(self.params.get("inertia", 0.20))
        self.state = np.zeros(6)
        # Actual (lagged) thrust fraction per thruster, 0..1.
        self.thrust_frac = np.zeros(self.n_thrusters)

        if self.mode.value == "experiment":
            self.teensy = TeensyInterface(
                self.params.get("teensy_port", "/dev/ttyACM0"))
            self.teensy.open()

    def teardown(self) -> None:
        if self.mode.value == "experiment" and getattr(self, "teensy", None):
            self.teensy.close()

    # -- shared: wrench -> duty cycles -------------------------------------- #

    def _duties_from_wrench(self, wrench) -> "Any":
        np = self.np
        w = np.asarray(wrench, dtype=float).reshape(3)
        f_des = self.B_pinv @ w                      # desired per-thruster force
        duty = np.clip(f_des / self.max_thrust, 0.0, 1.0)
        duty[duty < self.min_duty] = 0.0             # squelch dribble
        return duty

    @staticmethod
    def _wrench_from_command(command: Dict[str, Any]) -> List[float]:
        """Pull a [Fx, Fy, Mz] wrench out of the controller command."""
        if "wrench" in command:
            return list(command["wrench"])
        return [command.get("Fx", 0.0), command.get("Fy", 0.0),
                command.get("Mz", 0.0)]

    # -- experiment mode ---------------------------------------------------- #

    def step_experiment(self, t: float, dt: float, command: Dict[str, Any]):
        wrench = self._wrench_from_command(command)
        duty = self._duties_from_wrench(wrench)
        self.teensy.send_duties(duty.tolist())
        return {"duty": duty.tolist(), "wrench_cmd": wrench}

    # -- simulation mode ---------------------------------------------------- #

    def _actuation_model(self, duty, dt: float):
        """First-order solenoid open/close lag toward the commanded duty.

        Models finite valve timing + thrust decay: when commanded open the
        actual thrust fraction rises with ``tau_open``; when commanded closed it
        falls with ``tau_close``. Returns the realized thrust fraction (0..1).
        """
        np = self.np
        rising = duty > self.thrust_frac
        tau = np.where(rising, self.tau_open, self.tau_close)
        alpha = 1.0 - np.exp(-dt / np.maximum(tau, 1e-6))
        self.thrust_frac = self.thrust_frac + alpha * (duty - self.thrust_frac)
        return np.clip(self.thrust_frac, 0.0, 1.0)

    def step_simulation(self, t: float, dt: float, command: Dict[str, Any]):
        np = self.np
        wrench_cmd = self._wrench_from_command(command)
        duty = self._duties_from_wrench(wrench_cmd)

        # Duty -> realized per-thruster force (lagged, saturated).
        frac = self._actuation_model(duty, dt)
        f_real = frac * self.max_thrust

        # Realized, saturated body wrench: w_real = B @ f_real.
        w_real = self.B @ f_real                     # [Fx, Fy, Mz] in body frame

        # Rotate body-frame force into the inertial frame, then F = ma.
        theta = self.state[2]
        c, s = np.cos(theta), np.sin(theta)
        Fx_b, Fy_b, Mz = w_real
        Fx_i = c * Fx_b - s * Fy_b
        Fy_i = s * Fx_b + c * Fy_b

        ax, ay = Fx_i / self.mass, Fy_i / self.mass
        alpha_yaw = Mz / self.inertia

        # Semi-implicit Euler integration.
        self.state[3] += ax * dt
        self.state[4] += ay * dt
        self.state[5] += alpha_yaw * dt
        self.state[0] += self.state[3] * dt
        self.state[1] += self.state[4] * dt
        self.state[2] += self.state[5] * dt

        return {
            "duty": duty.tolist(),
            "wrench_cmd": list(wrench_cmd),
            "wrench_real": w_real.tolist(),
            "pose": self.state[:3].tolist(),
            "twist": self.state[3:].tolist(),
        }

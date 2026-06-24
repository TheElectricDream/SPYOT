"""The Component abstraction - the heart of SPYOT's flexibility.

Every physical or logical part of the spacecraft (thrusters, reaction wheel,
metrology, dynamics, ...) is a ``Component``. A component declares how it
behaves in two modes:

* ``simulation`` - the component models itself in software. For a thruster
  system this means: command wrench -> duty cycles -> solenoid actuation model
  -> realized (saturated) wrench -> spacecraft dynamics.

* ``experiment`` - the component talks to real hardware. For a thruster system
  this means: command wrench -> duty cycles -> serial packet to a Teensy
  running a hardware-PWM firmware.

Subclasses implement ``step_simulation`` and/or ``step_experiment``. The base
class's :meth:`Component.step` dispatches to the right one based on ``mode``.
This is the template you copy whenever you add a new hardware interface.
"""

from __future__ import annotations

import enum
from abc import ABC
from typing import Any, Dict, Optional


class ComponentMode(str, enum.Enum):
    SIMULATION = "simulation"
    EXPERIMENT = "experiment"


class Component(ABC):
    """Base class for every spacecraft subsystem.

    Lifecycle, called by the runner:
        1. ``setup()``                      once, before the loop
        2. ``step(t, dt, command)``         every tick
        3. ``teardown()``                   once, after the loop (always)
    """

    def __init__(self, name: str, mode: str | ComponentMode,
                 params: Optional[Dict[str, Any]] = None) -> None:
        self.name = name
        self.mode = ComponentMode(mode)
        self.params = params or {}

    # -- lifecycle ---------------------------------------------------------- #

    def setup(self) -> None:
        """Acquire resources (open serial ports, allocate state, ...)."""

    def teardown(self) -> None:
        """Release resources. Always called, even if the loop errors out."""

    # -- per-tick dispatch -------------------------------------------------- #

    def step(self, t: float, dt: float,
             command: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Advance the component by one tick and return its output telemetry.

        ``t``  - simulation time since start (s)
        ``dt`` - nominal timestep (s)
        ``command`` - inputs from the controller / other components
        """
        if self.mode is ComponentMode.EXPERIMENT:
            return self.step_experiment(t, dt, command or {})
        return self.step_simulation(t, dt, command or {})

    # Subclasses override one or both of these. Defaults raise so that a
    # misconfigured mode fails loudly instead of silently doing nothing.
    def step_simulation(self, t: float, dt: float,
                        command: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement simulation mode")

    def step_experiment(self, t: float, dt: float,
                        command: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement experiment mode")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{type(self).__name__} name={self.name!r} mode={self.mode.value}>"

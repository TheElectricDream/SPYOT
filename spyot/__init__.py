"""SPYOT - Simulation and PhYsical Operations Testbed for planar spacecraft.

SPYOT is a small, flexible framework for running *both* simulations and
hardware-in-the-loop experiments of planar (3-DOF: x, y, yaw) spacecraft.

The package is split into two halves:

* ``spyot.manager`` - the *manager*. A cross-platform controller that handles
  user configuration, Python virtual-environment creation, deployment of the
  software to remote machines, and time-synchronized experiment launches.

* ``spyot.core`` / ``spyot.components`` - the *software*. The thing that
  actually runs on each spacecraft computer: a precise fixed-rate loop that
  steps a set of pluggable ``Component`` objects, each of which knows how to
  behave in ``simulation`` mode and in ``experiment`` mode.
"""

__version__ = "0.1.0"

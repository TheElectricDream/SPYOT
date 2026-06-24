"""Built-in SPYOT components and the component registry.

To add a new subsystem:
    1. Subclass ``spyot.core.Component`` (copy ``thrusters.py`` as a template).
    2. Register it in ``REGISTRY`` below under a short ``type`` name.
    3. Reference that ``type`` from your config's ``components`` list.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from spyot.core.component import Component
from spyot.components.heartbeat import Heartbeat
from spyot.components.thrusters import PlanarThrusterSystem

# type name -> Component subclass
REGISTRY: Dict[str, Type[Component]] = {
    "heartbeat": Heartbeat,
    "thrusters": PlanarThrusterSystem,
}


def build_component(name: str, type_: str, mode: str,
                    params: Dict[str, Any]) -> Component:
    """Instantiate a component from its declarative spec."""
    try:
        cls = REGISTRY[type_]
    except KeyError as exc:
        raise KeyError(
            f"unknown component type {type_!r}; "
            f"known types: {sorted(REGISTRY)}"
        ) from exc
    return cls(name=name, mode=mode, params=params)


__all__ = ["REGISTRY", "build_component", "Heartbeat", "PlanarThrusterSystem"]

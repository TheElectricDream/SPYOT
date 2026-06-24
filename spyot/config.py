"""Configuration model and loading for SPYOT.

The user edits a human-friendly YAML file (see ``config/default.yaml``). The
manager parses it into the dataclasses below. For *execution* the manager
compiles the relevant pieces into a plain JSON "run config" (see
:meth:`Config.to_runconfig`) so that the software running on a remote machine
only needs the standard library to read it - no YAML dependency required on the
targets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Host:
    """A remote spacecraft computer."""

    name: str
    ip: str
    user: str = "spot"
    password: Optional[str] = None
    # Interpreter to use *on the remote* when (re)building the venv / running.
    python: str = "python3"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Host":
        return cls(
            name=d["name"],
            ip=d["ip"],
            user=d.get("user", "spot"),
            password=d.get("password"),
            python=d.get("python", "python3"),
        )


@dataclass
class SyncConfig:
    """Parameters controlling clock-offset measurement and the start barrier."""

    # Seconds of lead time given to every instance to reach the start barrier.
    lead_time_s: float = 5.0
    # Number of round-trip handshakes used to estimate each host's clock offset.
    offset_samples: int = 15
    # Width (s) of the busy-wait spin at the end of each precise sleep.
    spin_window_s: float = 0.0015

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SyncConfig":
        return cls(
            lead_time_s=float(d.get("lead_time_s", 5.0)),
            offset_samples=int(d.get("offset_samples", 15)),
            spin_window_s=float(d.get("spin_window_s", 0.0015)),
        )


@dataclass
class ComponentSpec:
    """Declarative description of one piece of the spacecraft.

    ``type`` is looked up in the component registry (see
    ``spyot.components.registry``). ``params`` are passed straight to the
    component constructor.
    """

    name: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ComponentSpec":
        return cls(name=d["name"], type=d["type"], params=dict(d.get("params", {})))


@dataclass
class Config:
    """Top-level SPYOT configuration."""

    # --- Environment ---
    python_version: str = "3.11"
    venv_dir: str = ".venv"
    requirements: str = "requirements.txt"

    # --- Execution ---
    mode: str = "simulation"  # "simulation" or "experiment"
    sample_rate_hz: float = 10.0
    duration_s: Optional[float] = 10.0  # None -> run forever
    message: str = "SPYOT tick"

    # --- Components that make up the spacecraft ---
    components: List[ComponentSpec] = field(default_factory=list)

    # --- Deployment / sync ---
    remote_root: str = "~/spyot"
    sync: SyncConfig = field(default_factory=SyncConfig)
    hosts: List[Host] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        return cls(
            python_version=str(d.get("python_version", "3.11")),
            venv_dir=str(d.get("venv_dir", ".venv")),
            requirements=str(d.get("requirements", "requirements.txt")),
            mode=str(d.get("mode", "simulation")),
            sample_rate_hz=float(d.get("sample_rate_hz", 10.0)),
            duration_s=(None if d.get("duration_s", 10.0) in (None, "none", "None")
                        else float(d.get("duration_s", 10.0))),
            message=str(d.get("message", "SPYOT tick")),
            components=[ComponentSpec.from_dict(c) for c in d.get("components", [])],
            remote_root=str(d.get("remote_root", "~/spyot")),
            sync=SyncConfig.from_dict(d.get("sync", {})),
            hosts=[Host.from_dict(h) for h in d.get("hosts", [])],
        )

    def host_by_name(self, name: str) -> Host:
        for h in self.hosts:
            if h.name == name:
                return h
        raise KeyError(f"no host named {name!r} in config")

    def select_hosts(self, names: Optional[List[str]]) -> List[Host]:
        """Return the hosts named in ``names`` (or all hosts if ``names`` is None)."""
        if not names:
            return list(self.hosts)
        return [self.host_by_name(n) for n in names]

    def to_runconfig(self) -> Dict[str, Any]:
        """Compile the execution-relevant fields into a plain dict.

        This is what gets serialized to JSON and handed to the runner. It
        deliberately omits credentials and host lists.
        """
        return {
            "mode": self.mode,
            "sample_rate_hz": self.sample_rate_hz,
            "duration_s": self.duration_s,
            "message": self.message,
            "spin_window_s": self.sync.spin_window_s,
            "components": [
                {"name": c.name, "type": c.type, "params": c.params}
                for c in self.components
            ],
        }


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_config(path: str | Path) -> Config:
    """Load a Config from a YAML or JSON file.

    YAML is preferred for human editing but requires PyYAML. JSON is always
    supported via the standard library.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                f"Reading {path} requires PyYAML. Install it with "
                f"'pip install pyyaml', or convert your config to JSON."
            ) from exc
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)

    return Config.from_dict(data)

"""Deploy the SPYOT software + environment to remote spacecraft computers.

Because virtualenvs are not portable across machines, "deploying the
environment" means: upload the source tree, then *rebuild* the venv on the
target by running the same env logic remotely. After this, every selected host
has an identical, ready-to-run copy of the software.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

from spyot.config import Config, Host
from spyot.manager.ssh import SSHClient


# Files/dirs never shipped to remotes.
DEFAULT_EXCLUDES = (".git", ".venv", "__pycache__", ".mypy_cache",
                    ".pytest_cache", ".DS_Store")


@dataclass
class DeployResult:
    host: str
    ok: bool
    message: str


class Deployer:
    def __init__(self, config: Config, project_root: str | Path) -> None:
        self.config = config
        self.project_root = Path(project_root).resolve()

    def _remote_home_root(self, ssh: SSHClient) -> str:
        """Resolve the remote root, expanding a leading ``~``."""
        root = self.config.remote_root
        if root.startswith("~"):
            res = ssh.run("printf %s \"$HOME\"")
            home = res.stdout.strip() or "/root"
            root = root.replace("~", home, 1)
        return str(PurePosixPath(root))

    def deploy_one(self, host: Host, build_env: bool = True) -> DeployResult:
        try:
            with SSHClient(host.ip, host.user, host.password) as ssh:
                remote_root = self._remote_home_root(ssh)

                # 1. Upload source tree.
                ssh.mkdirs(remote_root)
                n = ssh.put_tree(self.project_root, remote_root,
                                 excludes=DEFAULT_EXCLUDES)

                # 2. Ship a compiled JSON run config (no YAML needed remotely).
                ssh.put_text(
                    json.dumps(self.config.to_runconfig(), indent=2),
                    str(PurePosixPath(remote_root) / "runconfig.json"),
                )

                msg = f"uploaded {n} files to {remote_root}"

                # 3. Rebuild the venv on the target using the manager CLI.
                if build_env:
                    cmd = (
                        f"cd {remote_root} && "
                        f"{host.python} manager.py env --config config/default.yaml"
                    )
                    res = ssh.run(cmd, timeout=900)
                    if not res.ok:
                        return DeployResult(
                            host.name, False,
                            f"{msg}; env build FAILED:\n{res.stdout}\n{res.stderr}")
                    tail = res.stdout.strip().splitlines()[-1:] or [""]
                    msg += f"; env built ({tail[0]})"

                return DeployResult(host.name, True, msg)
        except Exception as exc:  # noqa: BLE001 - report per-host, keep going
            return DeployResult(host.name, False, f"{type(exc).__name__}: {exc}")

    def deploy(self, hosts: Optional[List[Host]] = None,
               build_env: bool = True) -> List[DeployResult]:
        hosts = hosts if hosts is not None else self.config.hosts
        results: List[DeployResult] = []
        for h in hosts:
            print(f"[deploy] {h.name} ({h.ip}) ...", flush=True)
            r = self.deploy_one(h, build_env=build_env)
            status = "OK" if r.ok else "FAIL"
            print(f"[deploy] {h.name}: {status} - {r.message}", flush=True)
            results.append(r)
        return results

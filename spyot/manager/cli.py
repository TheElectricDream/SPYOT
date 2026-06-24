"""SPYOT manager command-line interface.

Subcommands
-----------
  init        write a starter config file
  config      show the resolved configuration
  env         check/create the local virtual environment
  run         run the software locally (synchronized start, precise loop)
  offsets     measure clock offsets to the selected hosts
  deploy      upload source + rebuild the venv on the selected hosts
  experiment  deploy (optional), then launch a synchronized run across hosts

Local operations (``env``, ``run``) use only the standard library so you can
start testing immediately. Remote operations (``deploy``, ``offsets``,
``experiment``) require paramiko (and YAML config needs PyYAML); pass
``--auto-install`` to fetch them automatically.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from spyot.manager import bootstrap

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default.yaml"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _load(args) -> "object":
    """Load config, ensuring YAML support if the file is YAML."""
    path = Path(args.config)
    if path.suffix.lower() in (".yaml", ".yml"):
        bootstrap.ensure(["yaml"], auto_install=getattr(args, "auto_install", False))
    from spyot.config import load_config
    return load_config(path)


def _select_host_names(args) -> Optional[List[str]]:
    return args.hosts.split(",") if getattr(args, "hosts", None) else None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(args) -> int:
    target = Path(args.config)
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    template = (PROJECT_ROOT / "config" / "default.yaml").read_text(encoding="utf-8")
    target.write_text(template, encoding="utf-8")
    print(f"wrote starter config to {target}")
    return 0


def cmd_config(args) -> int:
    cfg = _load(args)
    print(json.dumps(cfg.to_runconfig(), indent=2))
    print("\nhosts:")
    for h in cfg.hosts:
        print(f"  - {h.name}  {h.user}@{h.ip}  python={h.python}")
    return 0


def cmd_env(args) -> int:
    cfg = _load(args)
    from spyot.manager.environment import EnvironmentManager
    em = EnvironmentManager(
        project_root=PROJECT_ROOT,
        venv_dir=cfg.venv_dir,
        python_version=cfg.python_version,
        requirements=cfg.requirements,
    )
    if args.check:
        ok = em.is_valid()
        print(f"venv {'OK' if ok else 'MISSING/INVALID'} at {em.venv_path} "
              f"(have {em.current_version()}, want {cfg.python_version})")
        return 0 if ok else 1
    created, msg = em.ensure(force=args.force)
    print(f"[env] {'created' if created else 'unchanged'}: {msg}")
    return 0


def cmd_run(args) -> int:
    """Run the software locally inside the project venv."""
    cfg = _load(args)
    from spyot.manager.environment import EnvironmentManager
    em = EnvironmentManager(PROJECT_ROOT, cfg.venv_dir,
                            cfg.python_version, cfg.requirements)
    # Write a JSON run config the runner can read without YAML.
    runconfig_path = PROJECT_ROOT / "runconfig.json"
    runconfig_path.write_text(json.dumps(cfg.to_runconfig(), indent=2),
                              encoding="utf-8")

    python = str(em.venv_python) if (em.exists() and not args.system_python) \
        else sys.executable
    cmd = [python, "-m", "spyot.run", "--runconfig", str(runconfig_path),
           "--instance-name", args.instance_name]
    print(f"[run] {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def cmd_offsets(args) -> int:
    cfg = _load(args)
    bootstrap.ensure(["paramiko"], auto_install=args.auto_install)
    from spyot.manager.ssh import SSHClient
    from spyot.manager.sync import measure_offset
    hosts = cfg.select_hosts(_select_host_names(args))
    for h in hosts:
        try:
            with SSHClient(h.ip, h.user, h.password) as ssh:
                est = measure_offset(ssh, h, cfg.sync.offset_samples)
            print(f"{h.name:12s} offset={est.offset_s * 1e3:+8.3f} ms  "
                  f"rtt={est.rtt_s * 1e3:7.3f} ms  ({est.samples} samples)")
        except Exception as exc:  # noqa: BLE001
            print(f"{h.name:12s} ERROR: {type(exc).__name__}: {exc}")
    return 0


def cmd_deploy(args) -> int:
    cfg = _load(args)
    bootstrap.ensure(["paramiko"], auto_install=args.auto_install)
    from spyot.manager.deployment import Deployer
    hosts = cfg.select_hosts(_select_host_names(args))
    deployer = Deployer(cfg, PROJECT_ROOT)
    results = deployer.deploy(hosts, build_env=not args.no_env)
    ok = all(r.ok for r in results)
    print(f"\n[deploy] {sum(r.ok for r in results)}/{len(results)} hosts OK")
    return 0 if ok else 1


def cmd_experiment(args) -> int:
    cfg = _load(args)
    bootstrap.ensure(["paramiko"], auto_install=args.auto_install)
    hosts = cfg.select_hosts(_select_host_names(args))

    if not args.no_deploy:
        from spyot.manager.deployment import Deployer
        results = Deployer(cfg, PROJECT_ROOT).deploy(hosts, build_env=not args.no_env)
        if not all(r.ok for r in results):
            print("[experiment] aborting: deployment failed on one or more hosts")
            return 1

    from spyot.manager.sync import ExperimentLauncher
    print(f"[experiment] launching synchronized run on: "
          f"{', '.join(h.name for h in hosts)}")
    ExperimentLauncher(cfg).run_experiment(hosts)
    print("[experiment] all instances finished")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spyot-manager",
                                description="SPYOT manager")
    p.add_argument("--config", default=str(DEFAULT_CONFIG),
                   help=f"config file (default: {DEFAULT_CONFIG})")
    p.add_argument("--auto-install", action="store_true",
                   help="auto pip-install missing manager deps (paramiko, pyyaml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="write a starter config")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("config", help="show resolved config")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("env", help="check/create the local venv")
    s.add_argument("--check", action="store_true", help="only check, don't create")
    s.add_argument("--force", action="store_true", help="recreate from scratch")
    s.set_defaults(func=cmd_env)

    s = sub.add_parser("run", help="run the software locally")
    s.add_argument("--instance-name", default="local")
    s.add_argument("--system-python", action="store_true",
                   help="use the current interpreter instead of the project venv")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("offsets", help="measure clock offsets to hosts")
    s.add_argument("--hosts", help="comma-separated host names (default: all)")
    s.set_defaults(func=cmd_offsets)

    s = sub.add_parser("deploy", help="deploy source + venv to hosts")
    s.add_argument("--hosts", help="comma-separated host names (default: all)")
    s.add_argument("--no-env", action="store_true",
                   help="upload source only; skip rebuilding the remote venv")
    s.set_defaults(func=cmd_deploy)

    s = sub.add_parser("experiment",
                       help="deploy + launch a synchronized run across hosts")
    s.add_argument("--hosts", help="comma-separated host names (default: all)")
    s.add_argument("--no-deploy", action="store_true",
                   help="skip deployment; assume hosts are already up to date")
    s.add_argument("--no-env", action="store_true",
                   help="when deploying, skip rebuilding the remote venv")
    s.set_defaults(func=cmd_experiment)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, KeyError) as exc:
        # Expected, user-actionable errors (missing deps, unknown host, ...).
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

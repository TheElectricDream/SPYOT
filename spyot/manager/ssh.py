"""Thin SSH/SFTP helper built on paramiko.

paramiko is used (rather than shelling out to ``ssh``/``scp``/``sshpass``)
because it is pure Python and works identically on Windows, macOS, and Linux -
a hard requirement for the manager. It is imported lazily so that the
stdlib-only parts of the manager (env management, local run) work without it.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional


def _require_paramiko():
    try:
        import paramiko  # type: ignore
        return paramiko
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Remote operations require 'paramiko'. Install the manager "
            "dependencies:  pip install -r requirements-manager.txt  "
            "(or run the manager with --auto-install)."
        ) from exc


class RemoteResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class SSHClient:
    """Context-managed SSH connection to one host."""

    def __init__(self, host: str, user: str, password: Optional[str] = None,
                 port: int = 22, timeout: float = 10.0) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.timeout = timeout
        self._client = None
        self._sftp = None

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        paramiko = _require_paramiko()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            timeout=self.timeout,
            allow_agent=self.password is None,
            look_for_keys=self.password is None,
        )
        self._client = client

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- command execution -------------------------------------------------- #

    def run(self, command: str, timeout: Optional[float] = None) -> RemoteResult:
        assert self._client is not None, "not connected"
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return RemoteResult(code, out, err)

    def start(self, command: str):
        """Start a long-running command and return the paramiko channel.

        The caller is responsible for reading output and closing it. Used to
        launch synchronized experiment processes and stream their stdout.
        """
        assert self._client is not None, "not connected"
        transport = self._client.get_transport()
        channel = transport.open_session()
        channel.get_pty()
        channel.exec_command(command)
        return channel

    # -- file transfer ------------------------------------------------------ #

    def _sftp_client(self):
        if self._sftp is None:
            assert self._client is not None, "not connected"
            self._sftp = self._client.open_sftp()
        return self._sftp

    def mkdirs(self, remote_dir: str) -> None:
        sftp = self._sftp_client()
        parts = PurePosixPath(remote_dir).parts
        cur = ""
        for p in parts:
            cur = p if cur == "" else str(PurePosixPath(cur) / p)
            if cur in ("/", ""):
                continue
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError:
                    pass

    def put_tree(self, local_root: str | Path, remote_root: str,
                 excludes: Iterable[str] = ()) -> int:
        """Recursively upload ``local_root`` into ``remote_root``.

        Returns the number of files transferred. ``excludes`` are path-fragment
        substrings (e.g. ``.git``, ``.venv``) that are skipped.
        """
        sftp = self._sftp_client()
        local_root = Path(local_root).resolve()
        excludes = tuple(excludes)
        count = 0
        self.mkdirs(remote_root)
        for dirpath, dirnames, filenames in os.walk(local_root):
            rel = Path(dirpath).relative_to(local_root)
            # Prune excluded directories in-place.
            dirnames[:] = [d for d in dirnames
                           if not any(e in str(rel / d) or e == d for e in excludes)]
            remote_dir = str(PurePosixPath(remote_root) / rel.as_posix()) \
                if str(rel) != "." else remote_root
            self.mkdirs(remote_dir)
            for fn in filenames:
                lp = Path(dirpath) / fn
                relfile = str(rel / fn)
                if any(e in relfile for e in excludes):
                    continue
                rp = str(PurePosixPath(remote_dir) / fn)
                sftp.put(str(lp), rp)
                count += 1
        return count

    def put_text(self, text: str, remote_path: str) -> None:
        sftp = self._sftp_client()
        self.mkdirs(str(PurePosixPath(remote_path).parent))
        with sftp.open(remote_path, "w") as fh:
            fh.write(text)

"""Read per-customer secret files without exposing their values (ADR 0016)."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from auggie.contracts import CustomerScope

DEFAULT_ROOT = Path.home() / ".config" / "auggie" / "credentials"


class CredentialError(Exception):
    """A secret cannot be used: missing, unsafe to read, or badly named. Never carries the value."""


class Secret:
    """A secret value that never shows itself. str() and repr() are '***'."""

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "***"

    def __repr__(self) -> str:
        return "***"


class CredentialFiles:
    """Secrets as files, one folder per credential namespace (ADR 0016)."""

    def __init__(self, root: Path | None = None) -> None:
        """root defaults to $AUGGIE_CREDENTIALS_DIR, else DEFAULT_ROOT."""
        self._root = (
            root
            if root is not None
            else Path(os.environ.get("AUGGIE_CREDENTIALS_DIR", DEFAULT_ROOT))
        )

    @property
    def root(self) -> Path:
        return self._root

    def secret(self, scope: CustomerScope, name: str) -> Secret:
        """Read <root>/<scope.credential_namespace>/<name>."""
        namespace = scope.credential_namespace
        message = f"cannot read credential {namespace!r}/{name!r}"
        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", name) is None:
            raise CredentialError(message)
        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", namespace) is None:
            raise CredentialError(message)
        try:
            folder = self.root / namespace
            path = folder / name
            info = folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
                raise CredentialError(message)
            if not path.resolve().is_relative_to(folder.resolve()):
                raise CredentialError(message)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise CredentialError(message)
            # Pin the directory and refuse symlinks again at open, avoiding check/open races.
            directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(directory)
                if info.st_mode & 0o022:
                    raise CredentialError(message)
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_mode & 0o077
                    ):
                        raise CredentialError(message)
                    value = stream.read().decode("utf-8")
            finally:
                os.close(directory)
        except (OSError, ValueError, RuntimeError):
            raise CredentialError(message) from None
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        if not value:
            raise CredentialError(message)
        return Secret(value)

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

        def refused(reason: str) -> CredentialError:
            return CredentialError(f"cannot read credential {namespace!r}/{name!r}: {reason}")

        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", name) is None:
            raise refused("not a valid secret name")
        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", namespace) is None:
            raise refused("not a valid namespace")
        try:
            folder = self.root / namespace
            path = folder / name
            info = folder.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise refused("the namespace is not a real folder")
            if info.st_mode & 0o022:
                raise refused("the folder is writable by group or others")
            if not path.resolve().is_relative_to(folder.resolve()):
                raise refused("the path leaves the namespace folder")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise refused("not a regular file")
            # Pin the directory and refuse symlinks again at open, avoiding check/open races.
            directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(directory)
                if info.st_mode & 0o022:
                    raise refused("the folder is writable by group or others")
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise refused("not a regular file")
                    if info.st_uid != os.getuid():
                        raise refused("owned by another user")
                    if info.st_mode & 0o077:
                        raise refused("readable or writable by group or others; use mode 0600")
                    value = stream.read().decode("utf-8")
            finally:
                os.close(directory)
        except FileNotFoundError:
            raise refused("not found") from None
        except UnicodeDecodeError:
            raise refused("not UTF-8") from None
        except (OSError, ValueError, RuntimeError):
            raise refused("cannot be opened safely") from None
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        if not value:
            raise refused("empty")
        return Secret(value)

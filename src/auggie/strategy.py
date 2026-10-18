"""Load an approved strategy file and pin it to its exact revision (ADR 0013)."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from auggie.contracts import CustomerScope, ScopeViolation, Strategy, StrategyRef, sha256_digest


class StrategyError(ValueError):
    """A strategy file cannot be read or does not parse."""


@dataclass(frozen=True)
class LoadedStrategy:
    strategy: Strategy
    ref: StrategyRef


def _checked(path: str) -> PurePosixPath:
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StrategyError(f"strategy path {path!r} must stay inside the knowledge root")
    return relative


def _parse(data: bytes, path: str, commit: str, scope: CustomerScope) -> LoadedStrategy:
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise StrategyError(f"{path}: {error}") from error
    strategy = Strategy.model_validate(parsed)
    if strategy.customer_id != scope.customer_id:
        raise ScopeViolation(
            f"strategy {path!r} belongs to {strategy.customer_id!r}, not {scope.customer_id!r}"
        )
    return LoadedStrategy(
        strategy, StrategyRef(commit=commit, path=path, digest=sha256_digest(data))
    )


def load_strategy(root: Path, path: str, *, commit: str, scope: CustomerScope) -> LoadedStrategy:
    """Read ``path`` under the knowledge root ``root``, vouched for as ``commit``.

    For synthetic fixtures, which have no strategy repository. Real runs use
    ``load_strategy_at``, which reads the committed file itself.
    """
    return _parse((root / _checked(path)).read_bytes(), path, commit, scope)


def load_strategy_at(
    repository: Path, path: str, *, revision: str, scope: CustomerScope
) -> LoadedStrategy:
    """Read ``path`` exactly as it is committed at ``revision`` in a Git repository.

    The working tree is never read, so uncommitted edits cannot slip into a
    run. The ``StrategyRef`` names the full commit hash.
    """
    _checked(path)

    def git(*args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "--no-replace-objects", "-C", str(repository), *args],
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise StrategyError(f"cannot read {path!r} at {revision!r} in {repository}") from error

    commit = (
        git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").decode().strip()
    )
    return _parse(git("show", f"{commit}:{path}"), path, commit, scope)

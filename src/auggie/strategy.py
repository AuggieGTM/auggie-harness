"""Load an approved strategy file and pin it to its exact revision (ADR 0013)."""

from __future__ import annotations

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


def load_strategy(root: Path, path: str, *, commit: str, scope: CustomerScope) -> LoadedStrategy:
    """Read ``path`` under the knowledge root ``root`` as it is at ``commit``.

    The digest covers the file's exact bytes. Reading from a Git object at
    ``commit`` comes with the first real strategy repository; until then the
    caller vouches that ``root`` holds that revision.
    """
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StrategyError(f"strategy path {path!r} must stay inside the knowledge root")
    data = (root / relative).read_bytes()
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

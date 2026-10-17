"""The gate in front of every call that leaves the machine (ADR 0016)."""

from __future__ import annotations

from auggie.contracts import CustomerScope


class ExternalCallRefused(Exception):
    """The scope does not allow calls to external services. Nothing was sent."""


def require_external_calls(scope: CustomerScope) -> None:
    """Raise ``ExternalCallRefused`` unless ``scope`` allows external calls."""
    if not scope.execution.external_providers:
        raise ExternalCallRefused(
            f"scope {scope.customer_id!r} does not allow calls to external services"
        )

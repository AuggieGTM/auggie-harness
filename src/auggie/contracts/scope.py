"""Customer scope: the isolation boundary every customer-bound operation runs inside."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, model_validator

from auggie.contracts.approval import DeliveryPlatform
from auggie.contracts.common import Contract, Slug

Namespace = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")]

DeliveryMode = Literal["disabled", "simulated", "live"]


class ScopeViolation(ValueError):
    """A record or configuration crosses a customer boundary."""


class ExecutionBoundary(Contract):
    """What work a customer scope is allowed to perform.

    ``max_run_spend_micro_usd`` caps what one run may spend on paid calls, in
    millionths of a US dollar (ADR 0016). Zero allows no paid calls.
    ``platform`` is where a live scope sends from (ADR 0022).
    """

    delivery: DeliveryMode = "disabled"
    platform: DeliveryPlatform = "simulated"
    external_providers: bool = False
    max_run_spend_micro_usd: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _platform_fits_delivery(self) -> ExecutionBoundary:
        if self.delivery == "live" and self.platform == "simulated":
            raise ValueError("live delivery needs a real platform")
        if self.delivery == "simulated" and self.platform != "simulated":
            raise ValueError("simulated delivery uses the simulated platform")
        return self


class CustomerScope(Contract):
    """One customer's isolated credentials, data, knowledge and execution.

    The namespaces name where each kind of material lives; they hold no secret
    values. Distinct names do not prove isolation on their own, so deployments
    must enforce the boundary too. This contract only catches misconfiguration.
    """

    schema_version: Literal[1] = 1
    customer_id: Slug
    synthetic: bool
    data_namespace: Namespace
    credential_namespace: Namespace
    knowledge_namespace: Namespace
    execution: ExecutionBoundary = ExecutionBoundary()

    @model_validator(mode="after")
    def _synthetic_scopes_never_deliver(self) -> CustomerScope:
        if self.synthetic and self.execution.delivery == "live":
            raise ValueError("a synthetic scope cannot enable live delivery")
        return self

    def namespaces(self) -> tuple[str, str, str]:
        return (self.data_namespace, self.credential_namespace, self.knowledge_namespace)


class CustomerBound(Protocol):
    @property
    def customer_id(self) -> str: ...


def ensure_in_scope(scope: CustomerScope, record: CustomerBound) -> None:
    """Raise ``ScopeViolation`` unless ``record`` belongs to ``scope``'s customer."""
    if record.customer_id != scope.customer_id:
        raise ScopeViolation(
            f"record belongs to customer {record.customer_id!r}, "
            f"not the active scope {scope.customer_id!r}"
        )


def check_isolated(scopes: Iterable[CustomerScope]) -> None:
    """Raise ``ScopeViolation`` if two scopes share a customer ID or any namespace."""
    owners: dict[str, str] = {}
    customers: set[str] = set()
    for scope in scopes:
        if scope.customer_id in customers:
            raise ScopeViolation(f"customer {scope.customer_id!r} is configured twice")
        customers.add(scope.customer_id)
        for namespace in scope.namespaces():
            owner = owners.setdefault(namespace, scope.customer_id)
            if owner != scope.customer_id:
                raise ScopeViolation(
                    f"namespace {namespace!r} is shared by {owner!r} and {scope.customer_id!r}"
                )

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auggie.contracts import CustomerScope, ScopeViolation, check_isolated, ensure_in_scope
from tests.conftest import Scenario, variant


def make_scope(customer_id: str, *, synthetic: bool = True, **changes: object) -> CustomerScope:
    data: dict[str, object] = {
        "customer_id": customer_id,
        "synthetic": synthetic,
        "data_namespace": f"auggie_{customer_id.replace('-', '_')}",
        "credential_namespace": customer_id,
        "knowledge_namespace": f"{customer_id}-strategy",
    }
    data.update(changes)
    return CustomerScope.model_validate(data)


def test_defaults_disable_delivery_and_external_providers() -> None:
    scope = make_scope("demo-a")
    assert scope.execution.delivery == "disabled"
    assert scope.execution.external_providers is False


def test_synthetic_scope_cannot_deliver_live() -> None:
    with pytest.raises(ValidationError, match="synthetic scope cannot enable live delivery"):
        make_scope("demo-a", execution={"delivery": "live"})


def test_real_scope_may_enable_live_delivery() -> None:
    scope = make_scope("customer-a", synthetic=False, execution={"delivery": "live"})
    assert scope.execution.delivery == "live"


def test_unknown_fields_are_rejected(scenario: Scenario) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CustomerScope.model_validate(variant(scenario.scope, api_key="not-allowed"))


def test_contracts_are_immutable(scenario: Scenario) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        scenario.scope.customer_id = "someone-else"  # type: ignore[misc]


def test_ensure_in_scope_rejects_other_customers(scenario: Scenario) -> None:
    ensure_in_scope(scenario.scope, scenario.qualification)
    with pytest.raises(ScopeViolation, match="not the active scope"):
        ensure_in_scope(make_scope("demo-b"), scenario.qualification)


def test_check_isolated_accepts_disjoint_scopes() -> None:
    check_isolated([make_scope("demo-a"), make_scope("demo-b")])


def test_check_isolated_rejects_shared_namespace() -> None:
    shared = make_scope("demo-b", data_namespace="auggie_demo_a")
    with pytest.raises(ScopeViolation, match="shared by 'demo-a' and 'demo-b'"):
        check_isolated([make_scope("demo-a"), shared])


def test_check_isolated_rejects_duplicate_customer() -> None:
    with pytest.raises(ScopeViolation, match="configured twice"):
        check_isolated([make_scope("demo-a"), make_scope("demo-a")])


def test_runs_may_spend_nothing_unless_a_limit_is_set(scenario: Scenario) -> None:
    assert scenario.scope.execution.max_run_spend_micro_usd == 0
    limited = make_scope("demo-paid", execution={"max_run_spend_micro_usd": 5000})
    assert limited.execution.max_run_spend_micro_usd == 5000
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        make_scope("demo-paid", execution={"max_run_spend_micro_usd": -1})

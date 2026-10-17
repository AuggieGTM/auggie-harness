from __future__ import annotations

import pytest

from auggie.contracts import CustomerScope
from auggie.spend import RunBudget, SpendRefused
from tests.conftest import Scenario, variant


def test_a_scope_without_a_limit_allows_no_paid_calls(scenario: Scenario) -> None:
    budget = RunBudget.for_scope(scenario.scope)
    with pytest.raises(SpendRefused, match="limit of 0"):
        budget.reserve(1)


def test_the_limit_comes_from_the_scope(scenario: Scenario) -> None:
    paid = CustomerScope.model_validate(
        variant(scenario.scope, execution={"max_run_spend_micro_usd": 5000})
    )
    assert RunBudget.for_scope(paid).limit_micro_usd == 5000


def test_calls_are_refused_once_their_ceilings_could_exceed_the_limit() -> None:
    budget = RunBudget(5000)
    budget.reserve(2000)
    budget.reserve(2000)
    with pytest.raises(SpendRefused, match="4000 already committed"):
        budget.reserve(2000)
    assert budget.committed_micro_usd == 4000


def test_settling_releases_what_a_call_did_not_spend() -> None:
    budget = RunBudget(5000)
    first = budget.reserve(2000)
    second = budget.reserve(2000)
    assert budget.settle(first, 1000) == 1000
    assert budget.settle(second, 0) == 0
    budget.reserve(2000)
    assert (budget.spent_micro_usd, budget.committed_micro_usd) == (1000, 3000)


def test_an_unreported_charge_counts_as_the_full_ceiling() -> None:
    budget = RunBudget(5000)
    reservation = budget.reserve(2000)
    assert budget.settle(reservation, None) == 2000
    assert budget.spent_micro_usd == 2000


def test_a_reservation_settles_once_and_only_in_its_own_budget() -> None:
    budget = RunBudget(5000)
    reservation = budget.reserve(2000)
    with pytest.raises(ValueError, match="another budget"):
        RunBudget(5000).settle(reservation, 100)
    budget.settle(reservation, 100)
    with pytest.raises(ValueError, match="already settled"):
        budget.settle(reservation, 100)


@pytest.mark.parametrize("ceiling", [0, -5])
def test_a_paid_call_needs_a_positive_ceiling(ceiling: int) -> None:
    with pytest.raises(ValueError, match="positive ceiling"):
        RunBudget(5000).reserve(ceiling)


def test_charges_and_limits_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        RunBudget(-1)
    budget = RunBudget(5000)
    with pytest.raises(ValueError, match="cannot be negative"):
        budget.settle(budget.reserve(1000), -1)

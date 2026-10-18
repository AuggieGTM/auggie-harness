"""People are records with at most one plain address (ADR 0022)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auggie.contracts import Person


def person(**changes: object) -> Person:
    data: dict[str, object] = {
        "customer_id": "demo-msp",
        "person_id": "per-jordan-avery",
        "account_id": "acc-northstar",
        "first_name": "Jordan",
        "email": "jordan.avery@northstar.example",
    }
    data.update(changes)
    return Person.model_validate(data)


def test_a_person_may_have_one_plain_address() -> None:
    assert person().email == "jordan.avery@northstar.example"
    assert person(email=None).email is None


@pytest.mark.parametrize(
    "address",
    [
        "jordan@northstar.example, someone@else.example",
        "Jordan Avery <jordan@northstar.example>",
        "jordan@northstar.example\r\nBcc: someone@else.example",
        "jordan @northstar.example",
        "jordan@northstar",
        "@northstar.example",
        "jordan@@northstar.example",
    ],
    ids=["two", "display-name", "header-injection", "space", "no-dot", "no-local", "double-at"],
)
def test_anything_but_one_plain_address_is_refused(address: str) -> None:
    with pytest.raises(ValidationError):
        person(email=address)


def test_a_person_cannot_change() -> None:
    with pytest.raises(ValidationError):
        person().email = "other@northstar.example"  # type: ignore[misc]

"""People: who a draft is addressed to, kept as a record that is never edited (ADR 0022)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import StringConstraints

from auggie.contracts.common import Contract, RecordId, Slug

EmailAddress = Annotated[
    str,
    StringConstraints(
        max_length=254,
        pattern=(
            r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
            r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
        ),
    ),
]
"""One plain address. No display name, quotes, commas or whitespace, so it can only ever name a
single mailbox in a message header."""

PersonName = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Person(Contract):
    """A person at an account whom a draft can be addressed to.

    A new address is a new person record, so an approval naming this
    ``person_id`` always means this address.
    """

    schema_version: Literal[1] = 1
    customer_id: Slug
    person_id: RecordId
    account_id: RecordId
    first_name: PersonName
    email: EmailAddress | None = None

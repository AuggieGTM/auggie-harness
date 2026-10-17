"""Accounts and recipients a run works on.

They are inputs, like strategy: the Store does not hold them yet, and events
refer to them by ID only. Each account belongs to one customer.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import HttpUrl, StringConstraints

from auggie.contracts import Contract
from auggie.contracts.common import RecordId, Slug

Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Recipient(Contract):
    person_id: RecordId
    first_name: Name


class Account(Contract):
    """An account to research, with one URL per strategy source kind."""

    account_id: RecordId
    customer_id: Slug
    key: Slug
    """Short, readable handle used in synthetic record IDs, such as ``northstar``."""
    name: Name
    sources: dict[Slug, HttpUrl]
    recipient: Recipient

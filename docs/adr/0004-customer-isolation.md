# ADR 0004: Isolate each customer's credentials, data, knowledge and execution

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

The same harness serves several customers. Mixing one customer's strategy, research, contacts or
credentials with another's would be a serious breach of trust.

## Decision

Each customer gets a `CustomerScope` with four isolated boundaries:

| Boundary | Meaning |
|---|---|
| Credentials | Secrets resolve within the customer's credential namespace and are never shared. Records hold references, never values. |
| Data | One MongoDB database per customer (the data namespace). |
| Knowledge | Each customer's approved strategy lives in its own knowledge namespace. |
| Execution | Delivery mode (`disabled`, `simulated`, `live`) and external-provider access are set per scope. |

- Every customer-bound record carries `customer_id`. Operations bound to a scope reject records
  from another scope.
- No two scopes may share a customer ID or a namespace.
- Synthetic scopes cannot enable live delivery.
- Customer deployments differ by configuration, not by code forks.

## Consequences

- The contracts catch misconfiguration (`ensure_in_scope`, `check_isolated`). They are not a
  security boundary by themselves. Distinct names do not prove isolation; deployments must
  enforce it with separate credentials, database users and processes.
- Cross-customer analysis, if ever wanted, needs its own ADR and explicit authorization.

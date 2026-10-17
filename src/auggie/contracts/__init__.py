"""Versioned contracts shared by every part of the harness.

Contracts are data shapes plus the invariants that make them trustworthy.
They have no I/O and no dependency on storage, providers or scheduling.
"""

from auggie.contracts.approval import ArtifactRef, DeliveryTarget, HumanApproval
from auggie.contracts.artifact import Artifact
from auggie.contracts.common import Actor, Contract, StrategyRef, sha256_digest
from auggie.contracts.events import (
    EVENT_STAGE,
    REQUIRED_REFS,
    STAGES,
    OperationalEvent,
    RecordRef,
)
from auggie.contracts.evidence import SourceEvidence, SubjectRef
from auggie.contracts.qualification import (
    CitationError,
    Finding,
    QualificationDecision,
    verify_citations,
    verify_cited_evidence,
)
from auggie.contracts.scope import (
    CustomerScope,
    ExecutionBoundary,
    ScopeViolation,
    check_isolated,
    ensure_in_scope,
)
from auggie.contracts.strategy import (
    BUILTIN_FIELDS,
    HeadcountRange,
    JobPosting,
    Mentions,
    MergeField,
    Play,
    Source,
    Strategy,
)

__all__ = [
    "BUILTIN_FIELDS",
    "EVENT_STAGE",
    "REQUIRED_REFS",
    "STAGES",
    "Actor",
    "Artifact",
    "ArtifactRef",
    "CitationError",
    "Contract",
    "CustomerScope",
    "DeliveryTarget",
    "ExecutionBoundary",
    "Finding",
    "HeadcountRange",
    "HumanApproval",
    "JobPosting",
    "Mentions",
    "MergeField",
    "OperationalEvent",
    "Play",
    "QualificationDecision",
    "RecordRef",
    "ScopeViolation",
    "Source",
    "SourceEvidence",
    "Strategy",
    "StrategyRef",
    "SubjectRef",
    "check_isolated",
    "ensure_in_scope",
    "sha256_digest",
    "verify_citations",
    "verify_cited_evidence",
]

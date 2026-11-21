"""Typed data contracts shared across the whole pipeline.

Every stage of the workflow receives and returns one of these models.
They are the single source of truth for what "a company" / "a signal" / "a plan" means in this repo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Confidence(StrEnum):
    """Confidence level on a data point. Separates verified facts from AI-generated hypotheses."""

    VERIFIED = "verified"  # from a primary source (10-K, official press release)
    SECONDARY = "secondary"  # from a single aggregator (BuiltIn, LinkedIn)
    APPROX = "approx"  # sources disagree; a midpoint or best-guess
    UNKNOWN = "unknown"  # field was not observable


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SourceAttribution(BaseModel):
    """A pointer to where a claim came from."""

    provider: str
    url: str | None = None
    observed_at: datetime = Field(default_factory=_utcnow)


class Signal(BaseModel):
    """A dated buying signal observed about a company."""

    kind: Literal["sales_hiring", "new_sales_leadership", "revenue_expansion", "other"]
    description: str
    event_date: datetime | None = None  # None → undated → freshness = 0
    source: SourceAttribution
    confidence: Confidence = Confidence.SECONDARY


class Account(BaseModel):
    """A company returned by a discovery provider.

    Note the explicit `fields_missing` list - a missing employee count is observable state,
    not a reason to invent one.
    """

    model_config = ConfigDict(extra="forbid")

    account_id: str  # external id assigned by the provider
    domain: str  # canonical lowercase, no scheme, no www
    name: str
    employees: int | None = None
    industry: str | None = None
    hq_country: str | None = None  # ISO alpha-2
    hq_state: str | None = None
    source: SourceAttribution
    signals: list[Signal] = Field(default_factory=list)
    employees_confidence: Confidence = Confidence.UNKNOWN
    fields_missing: list[str] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _canonicalize_domain(cls, v: str) -> str:
        from .util import canonicalize_domain

        return canonicalize_domain(v)


class QualificationDecision(BaseModel):
    """The output of the ICP qualification stage."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    decision: Literal["qualified", "not_qualified", "insufficient_evidence"]
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    decided_by: Literal["rules", "llm", "hybrid"] = "rules"
    decided_at: datetime = Field(default_factory=_utcnow)


class CRMAccount(BaseModel):
    """A record as stored in the CRM. Minimal - what the mock CRM tracks."""

    crm_account_id: str
    domain: str
    name: str
    updated_at: datetime
    source: str  # which upstream workflow run created or updated it


class CRMContact(BaseModel):
    """A contact record as stored in the CRM."""

    crm_contact_id: str
    crm_account_id: str
    email: str | None  # masked / synthetic in the demo
    first_name: str | None
    last_name: str | None
    title: str | None
    updated_at: datetime


class ReconciliationMatch(BaseModel):
    """How a discovered account relates to the CRM state."""

    account_id: str
    match_type: Literal["none", "exact_domain", "normalized_domain", "ambiguous"]
    crm_account_id: str | None = None
    duplicates_in_crm: list[str] = Field(default_factory=list)


class ContactProposal(BaseModel):
    """A proposed new contact to attach to an account."""

    account_id: str
    persona: str
    first_name: str | None
    last_name: str | None
    title: str | None
    email: str | None  # may be masked / synthetic / unverified
    verified: bool = False
    source: SourceAttribution


class EnrichmentCost(BaseModel):
    """What one enrichment call cost (in integer cents)."""

    account_id: str
    persona: str
    cost_cents: int
    provider: str


class WritePlanStatus(StrEnum):
    DRAFT = "draft"  # under construction
    PROPOSED = "proposed"  # passed policy checks, waiting for human approval
    REJECTED = "rejected"  # human (or policy) rejected
    APPROVED = "approved"  # human approved, ready for sync
    SYNCED = "synced"  # successfully applied to CRM
    PARTIAL = "partial"  # some operations failed; others succeeded
    FAILED = "failed"  # all operations failed


class WritePlanEntry(BaseModel):
    """One row in a WritePlan - what we want to do about one discovered account."""

    account_id: str
    crm_action: Literal["create_account", "skip_existing", "update_account"]
    crm_account_id: str | None  # set if match_type != "none"
    contacts_to_create: list[ContactProposal] = Field(default_factory=list)
    policy_failures: list[str] = Field(default_factory=list)
    skipped: bool = False  # set true if this row cannot be promoted
    skip_reason: str | None = None
    enrichment_status: Literal[
        "done", "budget_exceeded", "provider_failed", "not_needed"
    ] = "not_needed"


class WritePlan(BaseModel):
    """A versioned bundle of proposed CRM operations awaiting approval."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    status: WritePlanStatus = WritePlanStatus.DRAFT
    entries: list[WritePlanEntry] = Field(default_factory=list)
    enrichment_budget_cents: int
    enrichment_spent_cents: int = 0

    @property
    def approvable_entries(self) -> list[WritePlanEntry]:
        """Entries that can be promoted to the CRM (not skipped, no policy failures)."""
        return [e for e in self.entries if not e.skipped and not e.policy_failures]


class WriteOperationResult(BaseModel):
    """Audit record for one CRM write."""

    account_id: str
    operation: Literal["create_account", "update_account", "create_contact", "skip"]
    # stale_plan: CRM state changed after approval (another writer created this domain);
    # the entry was not applied and needs a fresh plan.
    outcome: Literal["ok", "failed", "idempotent_noop", "stale_plan"]
    error: str | None = None
    attempt: int = 1
    at: datetime = Field(default_factory=_utcnow)

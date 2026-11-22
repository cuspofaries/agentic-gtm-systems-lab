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
    # Whether a signal source was searched for this account, and how it went. "No signal
    # found" is only a fact when a search ran and returned nothing (`no_result`).
    signal_search: Literal["not_searched", "no_result", "found", "failed", "budget_exhausted"] = "not_searched"

    @field_validator("domain")
    @classmethod
    def _canonicalize_domain(cls, v: str) -> str:
        from .util import canonicalize_domain

        return canonicalize_domain(v)


class SourcedClaim(BaseModel):
    """A fact a web page states, as extracted by the research loop, with its checks.

    A claim is not a fact about the account: it says what a page says. Whether it can
    fill a missing field is decided later, from all claims on that field (FactResolution).
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    field: str  # account field the claim is about, e.g. "employees"
    value: str  # as the page states it, e.g. "between 500 and 1,000"
    subject: str  # the company the page says it is about
    url: str
    quote: str
    page_ref: str | None = None  # id of the page read in this run, if it was read
    published_at: datetime | None = None  # page publication date, when the search gave one
    retrieved_at: datetime = Field(default_factory=_utcnow)
    # quote_found: the quote is in the text of a page read in this run and the page names
    # the company. Anything else cannot resolve a field.
    check: Literal["quote_found", "page_not_read", "quote_not_found", "subject_mismatch", "unparsable"]


class FactResolution(BaseModel):
    """What research established about one field the provider left missing."""

    model_config = ConfigDict(extra="forbid")

    field: str
    status: Literal["filled", "contradicted", "unresolved", "not_checked"]
    value: str | None = None  # the value the rules were re-run with, when filled
    claim_ids: list[str] = Field(default_factory=list)
    note: str = ""


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
    # Research only: the claims pages made, and what they established per missing field.
    # `hybrid` = the rules re-run on the provider data plus the fields research filled.
    claims: list[SourcedClaim] = Field(default_factory=list)
    resolutions: list[FactResolution] = Field(default_factory=list)


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
    verified: bool = False  # True only when a provider or a verifier confirmed the email
    # Normalized verdict on the email when one was checked: valid / risky / invalid / unknown.
    email_status: Literal["valid", "risky", "invalid", "unknown"] | None = None
    email_checks: list[str] = Field(default_factory=list)  # e.g. ["fullenrich:CATCH_ALL", "zerobounce:valid"]
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
    # Which CRM the plan was reconciled against. Plans saved before this field existed
    # load as "mock", which is the only CRM they could have used.
    crm_target: str = "mock"
    # Which instance of that CRM (e.g. "hubspot:12345678"). None on plans made before it
    # was recorded; such plans can only be synced to the mock.
    crm_identity: str | None = None
    # Buying-signal search, when a signal provider ran (thousandths of a dollar).
    signals_budget_millidollars: int | None = None
    signals_spent_millidollars: int = 0

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


# --- Opportunity intelligence ---------------------------------------------------------
# Separate from ICP qualification: an account can fit the ICP with no sign of the problem
# the offer solves, and that is not a negative fact about the account.


class Observation(BaseModel):
    """An event a page reports, checked by code. A quote proves what a page says, not that it is true."""

    model_config = ConfigDict(extra="forbid")

    obs_id: str
    event: str  # e.g. "sales_hiring", or a short label
    statement: str  # the fact asserted, in the agent's words
    subject: str  # the company the page says it is about
    url: str
    quote: str
    page_ref: str | None = None
    published_at: datetime | None = None  # page publication date (from the search)
    event_date_stated: str | None = None  # only if the quote itself states when it happened
    retrieved_at: datetime = Field(default_factory=_utcnow)
    check: Literal["quote_found", "page_not_read", "quote_not_found", "subject_mismatch", "date_not_in_quote"]
    stance: Literal["supports", "contradicts", "irrelevant", "unresolved"]
    pain_id: str | None = None  # the pain point the stance is about


class PainHypothesis(BaseModel):
    """Why an event could matter for the offer. An inference, labeled as one."""

    model_config = ConfigDict(extra="forbid")

    pain_id: str
    statement: str
    status: Literal["supported", "contradicted", "mixed", "untested"]
    supporting: list[str] = Field(default_factory=list)  # obs ids that passed their checks
    contradicting: list[str] = Field(default_factory=list)


class RoleHypothesis(BaseModel):
    """Who might care, from the GTM Brain. Never a person, a name or contact details."""

    model_config = ConfigDict(extra="forbid")

    role_id: str
    role: str
    title_hints: list[str]
    reason: str
    uncertainty: str = "Hypothetical role from the GTM Brain; no person identified or verified."


class OpportunityAssessment(BaseModel):
    """Is there evidence that this account has the problem this offer solves, right now?"""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    offer_id: str
    icp_decision: Literal["qualified", "not_qualified", "insufficient_evidence"]  # linked, never changed
    icp_decided_by: str
    assessed_by: Literal["agent", "baseline"]
    observed_events: list[Observation] = Field(default_factory=list)
    pain_hypotheses: list[PainHypothesis] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)  # agent's text, labeled as such
    unknowns: list[str] = Field(default_factory=list)
    buying_roles: list[RoleHypothesis] = Field(default_factory=list)
    assessment_status: Literal["evidence_backed", "hypothesis_only", "needs_research", "not_relevant",
                               "insufficient_evidence"]
    recommended_next_action: Literal["research_more", "review", "consider_persona_enrichment", "defer",
                                     "no_relevant_evidence"]
    next_action_reason: str
    coverage: Literal["not_attempted", "checked_no_result", "found", "incomplete", "failed"]
    agent_view: dict = Field(default_factory=dict)  # what the agent itself proposed, for comparison
    unsupported_claims: int = 0  # observations dropped by the checks
    llm_calls: int = 0
    llm_cents: int = 0
    exa_millidollars: int = 0
    trace: list[dict] = Field(default_factory=list)
    assessed_at: datetime = Field(default_factory=_utcnow)

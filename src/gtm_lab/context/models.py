"""Pydantic models for the versioned GTM context.

The context is plain YAML files on disk. These classes are the schema the loader validates
against. They are intentionally simple: no jinja, no inheritance tricks, no vector DB.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ICPBand(BaseModel):
    """Where the company sits.

    employee ranges are integer bounds and are *inclusive*.
    industry_keywords use substring match on the normalized (lowercase) industry label.
    """

    model_config = ConfigDict(extra="forbid")

    employees_min: int
    employees_max: int
    industry_keywords: list[str]  # any-of
    geographies: list[str]  # ISO alpha-2 or "US", etc.
    sales_motion: str  # free text - carried in reports, not compared


class BuyerPersona(BaseModel):
    """A named persona we want to find and enrich."""

    model_config = ConfigDict(extra="forbid")

    name: str  # e.g. "VP Sales"
    titles_any_of: list[str]  # substring match on title


class SignalPolicy(BaseModel):
    """Which signals count, and how fresh they must be."""

    model_config = ConfigDict(extra="forbid")

    relevant_kinds: list[str]  # Signal.kind values that boost qualification
    max_signal_age_days: int  # older than this → signal does not count


class ExclusionRule(BaseModel):
    """A pattern that disqualifies a candidate account."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str
    industry_keywords: list[str] = Field(default_factory=list)
    domain_suffixes: list[str] = Field(default_factory=list)


class Policy(BaseModel):
    """Operational guardrails that apply to every proposed CRM change."""

    model_config = ConfigDict(extra="forbid")

    enrichment_budget_cents: int
    require_signal_for_qualification: bool = False  # strict ICP-only or signal-triggered
    require_approval_before_crm_write: Literal[True] = True  # never flip to False
    min_evidence_count: int = 1  # how many evidence facts before `qualified`
    require_contact_email_for_create: bool = False


class CRMSettings(BaseModel):
    """Settings used only when writing to a real CRM."""

    model_config = ConfigDict(extra="forbid")

    # HubSpot portals with GDPR features enabled require a legal basis on every contact.
    # It is a compliance decision, so there is no default: set the exact HubSpot value
    # (for example "Not applicable" for synthetic test data). None = not sent.
    hubspot_contact_legal_basis: str | None = None


class DiscoverySettings(BaseModel):
    """Provider-specific search filters used by real discovery providers.

    The ICP band (employees, geographies) is always applied; these add the provider's own
    vocabulary for "what kind of company", which the ICP keywords cannot express.
    """

    model_config = ConfigDict(extra="forbid")

    pdl_industries: list[str] = Field(default_factory=list)  # e.g. ["computer software"]
    pdl_tags: list[str] = Field(default_factory=list)  # e.g. ["software as a service"]


class ContactSettings(BaseModel):
    """What to do with contact emails that a finder or verifier could not confirm."""

    model_config = ConfigDict(extra="forbid")

    # An email judged invalid is always removed. `risky` (catch-all, high probability) and
    # `unknown` emails are kept with their status by default, or removed with drop_email.
    on_unverified_email: Literal["keep", "drop_email"] = "keep"


class GTMContext(BaseModel):
    """The full GTM context, versioned and loadable in one call."""

    model_config = ConfigDict(extra="forbid")

    version: str
    company_positioning: str
    icp: ICPBand
    personas: list[BuyerPersona]
    signal_policy: SignalPolicy
    exclusions: list[ExclusionRule]
    policy: Policy
    crm: CRMSettings = Field(default_factory=CRMSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    contacts: ContactSettings = Field(default_factory=ContactSettings)

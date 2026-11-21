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

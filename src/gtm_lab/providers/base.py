"""Abstract provider interfaces.

These are the contract any real provider (Clay, Apollo, ZoomInfo, Crustdata, BuiltIn, etc.)
would have to implement to drop into the workflow. Keep them thin - real adapters do the
heavy lifting; the contract only asks for what the workflow genuinely uses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..models import Account, ContactProposal


@dataclass
class ProviderResult:
    """Carries both the data returned and an explicit record of what was searched and failed.

    `source_failures` is a first-class part of the contract: a workflow that doesn't know
    which providers failed can't honestly report coverage.
    """

    accounts: list[Account] = field(default_factory=list)
    contacts: list[ContactProposal] = field(default_factory=list)
    source_failures: list[str] = field(default_factory=list)
    search_coverage: dict[str, int] = field(default_factory=dict)  # provider → rows returned
    # What the call actually cost, when the provider knows it (e.g. credits reported in a
    # response header). None = the budget is charged the provider's announced maximum.
    cost_cents: int | None = None


class DiscoveryProvider(ABC):
    """Yields candidate Accounts from an external data source."""

    name: str = "discovery"

    @abstractmethod
    def discover(self, *, limit: int) -> ProviderResult: ...


class EnrichmentProvider(ABC):
    """Returns ContactProposals for a given account and persona.

    The provider announces the most one call can cost, in integer cents. The budget enforcer
    checks that maximum before calling and charges the actual cost afterwards (or the
    maximum if the provider cannot tell). The provider itself does NOT enforce the budget.
    """

    name: str = "enrichment"
    cost_cents_per_call: int = 10  # worst case for one call

    @property
    def max_cost_cents_per_call(self) -> int:
        return self.cost_cents_per_call

    @abstractmethod
    def enrich(self, account: Account, personas: Iterable[str]) -> ProviderResult: ...

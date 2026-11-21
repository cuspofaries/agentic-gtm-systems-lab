"""Enrichment provider backed by a YAML fixture file.

For each (domain, persona) pair the fixture declares what the provider would return.
If a pair is not present, the provider returns an empty result for it - exactly the
behaviour a real provider has when it doesn't know anybody matching the query.

Emails in the fixture are synthetic (example.invalid / masked) - see tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..models import Account, ContactProposal, SourceAttribution
from .base import EnrichmentProvider, ProviderResult


class FixtureEnrichmentProvider(EnrichmentProvider):
    name = "fixture-enrichment"
    cost_cents_per_call = 10  # synthetic price for the demo

    def __init__(self, fixture_path: str | Path):
        self.fixture_path = Path(fixture_path)
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Enrichment fixture not found: {self.fixture_path}")
        data = yaml.safe_load(self.fixture_path.read_text()) or {}
        self._by_domain: dict[str, dict[str, list[dict]]] = {}
        for domain, personas in (data.get("by_domain", {}) or {}).items():
            self._by_domain[domain.lower()] = personas or {}
        self._provider_fails_for: set[str] = set(data.get("fails_for_domains", []))

    def enrich(self, account: Account, personas: Iterable[str]) -> ProviderResult:
        if account.domain in self._provider_fails_for:
            return ProviderResult(source_failures=[f"{self.name}:{account.domain}"])

        contacts: list[ContactProposal] = []
        hits_by_persona = self._by_domain.get(account.domain, {})
        for persona in personas:
            for row in hits_by_persona.get(persona, []):
                contacts.append(
                    ContactProposal(
                        account_id=account.account_id,
                        persona=persona,
                        first_name=row.get("first_name"),
                        last_name=row.get("last_name"),
                        title=row.get("title"),
                        email=row.get("email"),
                        verified=bool(row.get("verified", False)),
                        source=SourceAttribution(
                            provider=self.name,
                            observed_at=datetime.now(UTC),
                        ),
                    )
                )
        return ProviderResult(contacts=contacts, search_coverage={self.name: len(contacts)})

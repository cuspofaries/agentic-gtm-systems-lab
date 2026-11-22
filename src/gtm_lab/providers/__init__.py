"""Provider abstractions. Real data-provider adapters plug in here."""

from .base import DiscoveryProvider, EnrichmentProvider, ProviderResult
from .fixture_discovery import FixtureDiscoveryProvider
from .fixture_enrichment import FixtureEnrichmentProvider
from .pdl import PDLCompanyDiscoveryProvider, PDLEnrichmentProvider

__all__ = [
    "DiscoveryProvider",
    "EnrichmentProvider",
    "FixtureDiscoveryProvider",
    "FixtureEnrichmentProvider",
    "PDLCompanyDiscoveryProvider",
    "PDLEnrichmentProvider",
    "ProviderResult",
]

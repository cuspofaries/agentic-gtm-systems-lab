"""Discovery provider backed by a YAML fixture file.

This is the default provider for the offline demo. It demonstrates every edge the real
workflow must handle: missing fields, duplicate rows, varying confidence, source failures.

The fixture is NOT a representation of real companies - it is a didactic synthetic dataset.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..models import Account, Signal, SourceAttribution
from .base import DiscoveryProvider, ProviderResult


class FixtureDiscoveryProvider(DiscoveryProvider):
    """Reads accounts from a YAML file. Each top-level key is a provider identifier."""

    name = "fixture-discovery"

    def __init__(self, fixture_path: str | Path):
        self.fixture_path = Path(fixture_path)
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Discovery fixture not found: {self.fixture_path}")

    def discover(self, *, limit: int) -> ProviderResult:
        data = yaml.safe_load(self.fixture_path.read_text()) or {}
        accounts_raw = data.get("accounts", [])
        failures = list(data.get("source_failures", []))
        coverage: dict[str, int] = {}

        accounts: list[Account] = []
        for row in accounts_raw[:limit]:
            provider_name = row.get("source_provider", self.name)
            signals = [
                Signal(
                    kind=s["kind"],
                    description=s["description"],
                    event_date=_parse_dt(s.get("event_date")),
                    source=SourceAttribution(
                        provider=s.get("source_provider", provider_name),
                        url=s.get("source_url"),
                        observed_at=_parse_dt(s.get("observed_at")) or datetime.now(UTC),
                    ),
                    confidence=s.get("confidence", "secondary"),
                )
                for s in row.get("signals", [])
            ]
            accounts.append(
                Account(
                    account_id=row["account_id"],
                    domain=row["domain"],
                    name=row["name"],
                    employees=row.get("employees"),
                    industry=row.get("industry"),
                    hq_country=row.get("hq_country"),
                    hq_state=row.get("hq_state"),
                    source=SourceAttribution(
                        provider=provider_name,
                        url=row.get("source_url"),
                        observed_at=_parse_dt(row.get("observed_at")) or datetime.now(UTC),
                    ),
                    signals=signals,
                    # The fixture is a complete signal source for its accounts.
                    signal_search=row.get("signal_search", "found" if signals else "no_result"),
                    employees_confidence=row.get("employees_confidence", "unknown"),
                    fields_missing=list(row.get("fields_missing", [])),
                )
            )
            coverage[provider_name] = coverage.get(provider_name, 0) + 1

        return ProviderResult(accounts=accounts, source_failures=failures, search_coverage=coverage)


def _parse_dt(v: str | None) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    # Tolerant parsing - accept Z suffix or naive ISO strings
    s = v.rstrip("Z")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO datetime: {v!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

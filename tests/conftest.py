"""Pytest fixtures shared across the suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_lab.agents.qualifier import DeterministicQualifier
from gtm_lab.context import load_context
from gtm_lab.crm.mock import MockCRM
from gtm_lab.observability.events import NullRecorder
from gtm_lab.persistence.store import RunStore
from gtm_lab.providers import FixtureDiscoveryProvider, FixtureEnrichmentProvider
from gtm_lab.workflows import AccountDiscoveryWorkflow

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "account-discovery-to-crm"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def context():
    return load_context(EXAMPLE_DIR / "config" / "gtm-context.yaml")


@pytest.fixture
def make_workflow(state_dir: Path, context):
    """Build a workflow over the shared on-disk state.

    Calling it twice gives two independent objects (fresh CRM handle, fresh recorder)
    over the same SQLite files, which is how a process restart looks to the code.
    """
    seeded = False

    def _make(ctx=None, fallback_qualifier=None) -> AccountDiscoveryWorkflow:
        nonlocal seeded
        crm = MockCRM(state_dir / "crm.sqlite")
        if not seeded:
            crm.create_account(
                domain="axiom.saas.example",
                name="Axiom Workflow (seeded)",
                source="seed",
                external_ref="seed:axiom",
            )
            seeded = True
        return AccountDiscoveryWorkflow(
            context=ctx or context,
            discovery=FixtureDiscoveryProvider(EXAMPLE_DIR / "fixtures" / "discovery.yaml"),
            enrichment=FixtureEnrichmentProvider(EXAMPLE_DIR / "fixtures" / "enrichment.yaml"),
            crm=crm,
            store=RunStore(state_dir / "runs.sqlite"),
            recorder=NullRecorder(),
            qualifier=DeterministicQualifier(),
            fallback_qualifier=fallback_qualifier,
        )

    return _make


@pytest.fixture
def workflow(make_workflow) -> AccountDiscoveryWorkflow:
    return make_workflow()


@pytest.fixture
def store(workflow) -> RunStore:
    return workflow.store


@pytest.fixture
def crm(workflow) -> MockCRM:
    return workflow.crm

"""Build, run and summarize the real-data case. Shared by the opt-in test and its CI self-test."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from gtm_lab.agents.qualifier import DeterministicQualifier
from gtm_lab.context import load_context
from gtm_lab.crm import hubspot as hubspot_mod
from gtm_lab.crm.hubspot import HubSpotCRM
from gtm_lab.observability.events import NullRecorder
from gtm_lab.persistence.store import RunStore
from gtm_lab.providers import email as email_mod
from gtm_lab.providers import pdl as pdl_mod
from gtm_lab.providers import signals as signals_mod
from gtm_lab.providers.base import DiscoveryProvider
from gtm_lab.providers.email import EmailPipeline, FullEnrichFinder, ZeroBounceVerifier
from gtm_lab.providers.pdl import PDLCompanyDiscoveryProvider, PDLEnrichmentProvider
from gtm_lab.providers.signals import ExaSignalProvider
from gtm_lab.util import canonicalize_domain
from gtm_lab.workflows import AccountDiscoveryWorkflow

from .cassette import Cassette

KEY_VARS = ("PDL_API_KEY", "FULLENRICH_API_KEY", "ZEROBOUNCE_API_KEY", "HUBSPOT_SERVICE_KEY")
OPTIONAL_KEY_VARS = ("EXA_API_KEY",)  # only when expected.yaml sets `run.signals: exa`
REPLAY_KEYS = dict.fromkeys(KEY_VARS + OPTIONAL_KEY_VARS, "replay")  # connectors refuse an empty key


class _Capture(DiscoveryProvider):
    """Keeps the discovered accounts so results can be reported by domain."""

    def __init__(self, inner):
        self.inner, self.name, self.accounts = inner, inner.name, {}

    def discover(self, *, limit):
        r = self.inner.discover(limit=limit)
        self.accounts = {a.account_id: a for a in r.accounts}
        return r


def build(case: Path, cassette: Cassette, run: dict, keys: dict, state_dir: Path, *, no_wait: bool = False):
    ctx = load_context(case / "config" / "gtm-context.yaml")
    skip_waits = no_wait or cassette.mode == "replay"  # replay never waits on rate limits or polls
    nosleep = {"sleep": (lambda _s: None)} if skip_waits else {}
    pdl_kw = {"transport": cassette.pdl(pdl_mod.urllib_transport), **nosleep}
    if skip_waits:
        pdl_kw["clock"] = lambda: 0.0
    discovery = _Capture(PDLCompanyDiscoveryProvider(keys["PDL_API_KEY"], ctx, **pdl_kw))
    people = PDLEnrichmentProvider(
        keys["PDL_API_KEY"], ctx.personas, profiles_per_account=run["profiles_per_account"],
        title_levels=run.get("title_levels"), **pdl_kw)
    finder = FullEnrichFinder(
        keys["FULLENRICH_API_KEY"], cents_per_credit=run["email_finder_cents"],
        transport=cassette.email("fullenrich", email_mod.urllib_transport), **nosleep)
    verifier = ZeroBounceVerifier(
        keys["ZEROBOUNCE_API_KEY"], cents_per_credit=run["email_verifier_cents"],
        transport=cassette.email("zerobounce", email_mod.urllib_transport))
    crm = HubSpotCRM(keys["HUBSPOT_SERVICE_KEY"], contact_legal_basis=ctx.crm.hubspot_contact_legal_basis,
                     transport=cassette.hubspot(hubspot_mod.urllib_transport), **nosleep)
    def as_of():
        return cassette.recorded_at

    signals = None
    if run.get("signals") == "exa":
        signals = ExaSignalProvider(keys["EXA_API_KEY"], max_results=ctx.signals.max_results_per_account,
                                    transport=cassette.email("exa", signals_mod.urllib_transport),
                                    now=as_of, **nosleep)
    wf = AccountDiscoveryWorkflow(
        context=ctx, discovery=discovery, signals=signals,
        enrichment=EmailPipeline(people, finder=finder, verifier=verifier,
                                 max_contacts_per_call=run["profiles_per_account"],
                                 on_unverified=ctx.contacts.on_unverified_email),
        crm=crm, store=RunStore(state_dir / "runs.sqlite"), recorder=NullRecorder(),
        qualifier=DeterministicQualifier(now=as_of))
    return wf, discovery


def observe(plan, discovery: _Capture, cassette: Cassette) -> dict:
    """The run reduced to what a person checks: cost, CRM overlap, contacts, emails."""
    domain = {aid: canonicalize_domain(a.domain) for aid, a in discovery.accounts.items()}
    contacts = [c for e in plan.entries for c in e.contacts_to_create]
    return {
        "credits": cassette.credits(),
        "enrichment_spent_cents": plan.enrichment_spent_cents,
        "accounts_discovered": len(discovery.accounts),
        "skip_existing": sorted(domain[e.account_id] for e in plan.entries if e.crm_action == "skip_existing"),
        "create_account": sorted(domain[e.account_id] for e in plan.entries
                                 if e.crm_action == "create_account" and not e.skipped),
        "approvable": sorted(domain[e.account_id] for e in plan.approvable_entries),
        "enrichment_status": {domain[e.account_id]: e.enrichment_status for e in plan.entries},
        "contacts_proposed": len(contacts),
        "emails_found": sum(1 for c in contacts if c.email),
        "emails_by_status": {s: sum(1 for c in contacts if c.email_status == s)
                             for s in ("valid", "risky", "invalid", "unknown")},
        "signals_by_kind": dict(sorted(Counter(s.kind for a in discovery.accounts.values()
                                               for s in a.signals if s.source.provider == "exa").items())),
        "signals_spent_millidollars": plan.signals_spent_millidollars,
        "crm_writes_during_plan": cassette.writes(),
    }




def run_case(case: Path, mode: str, keys: dict, state_dir: Path, *,
             no_wait: bool = False) -> tuple[dict, dict | None]:
    """Plan the case once. Returns (observed, differences from expected.yaml's `expect`).

    Record mode saves the cassette; both modes write observed.yaml next to expected.yaml.
    Raises AssertionError on what must never happen: a CRM write while planning, an
    overspent budget, or (on replay) recorded calls the code no longer makes.
    """
    spec = yaml.safe_load((case / "expected.yaml").read_text())
    run = spec["run"]
    secrets = tuple(keys.values()) if mode == "record" else ()
    cassette = Cassette(case / "cassette.json", mode, secrets=secrets)
    wf, discovery = build(case, cassette, run, keys, state_dir, no_wait=no_wait)
    plan = wf.plan(limit=run["limit"], run_id="real_case")
    observed = observe(plan, discovery, cassette)
    assert observed["crm_writes_during_plan"] == [], "planning must not change the CRM"
    if mode == "record":
        cassette.save()
    else:
        assert not cassette.unused(), f"recorded calls not made on replay: {cassette.unused()}"
    (case / "observed.yaml").write_text(yaml.safe_dump(observed, sort_keys=False))
    assert plan.enrichment_spent_cents <= plan.enrichment_budget_cents
    expect = spec.get("expect")
    if expect is None:
        return observed, None
    diffs = {k: {"expected": v, "observed": observed.get(k)}
             for k, v in expect.items() if observed.get(k) != v}
    return observed, diffs

"""Opportunity assessment: deterministic baseline and the bounded investigator, with a
scripted Claude client and a fake web. The ICP decision is an input, never changed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gtm_lab.agents.opportunity import OpportunityInvestigator, baseline_assessment
from gtm_lab.agents.research_tools import EXA_CONTENTS_URL
from gtm_lab.models import Account, Confidence, QualificationDecision, Signal, SourceAttribution
from gtm_lab.providers.signals import EXA_SEARCH_URL
from tests.unit.test_research_loop import ScriptedClaude, _resp, _use

NOW = datetime(2026, 11, 22, tzinfo=UTC)
JOB = "https://jobs.example/acme/sdr"
JOB_TEXT = ("Acme Analytics is hiring five Sales Development Representatives. You will join a structured "
            "onboarding program; we are rebuilding sales onboarding to cut ramp time as the team doubles.")


class FakeWeb:
    """A tiny web: search returns the same results whatever the query, read returns page text."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []  # url -> (title, published, text)

    def __call__(self, method, url, headers, body):
        req = json.loads(body)
        self.calls.append(url)
        if url == EXA_SEARCH_URL:
            return 200, {"results": [{"url": u, "title": t, "publishedDate": d, "highlights": [x[:80]]}
                                     for u, (t, d, x) in self.pages.items()], "costDollars": {"total": 0.004}}
        assert url == EXA_CONTENTS_URL
        _title, _date, x = self.pages[req["urls"][0]]
        return 200, {"results": [{"url": req["urls"][0], "text": x}], "costDollars": {"total": 0.001}}


def _account(signal=True):
    sigs = [Signal(kind="sales_hiring", description="SDR - Acme Analytics", event_date=NOW - timedelta(days=5),
                   source=SourceAttribution(provider="exa", url=JOB))] if signal else []
    return Account(account_id="A1", domain="acme.example", name="Acme Analytics", employees=85,
                   employees_confidence=Confidence.VERIFIED, industry="B2B SaaS Software", hq_country="US",
                   signals=sigs, signal_search="found" if signal else "no_result",
                   source=SourceAttribution(provider="test"))


def _decision(d="qualified"):
    return QualificationDecision(account_id="A1", decision=d, explanation="rules", confidence=0.8)


def _obs(quote, stance="supports", pain="onboarding-at-scale", url=JOB, date=""):
    return {"event": "sales_hiring", "statement": "hiring SDRs with an onboarding rebuild", "subject": "Acme Analytics",
            "url": url, "quote": quote, "event_date_stated": date, "stance": stance, "pain_id": pain}


def _submit(i, *obs, action="consider_persona_enrichment"):
    return _resp(_use("submit", i, observations=list(obs), alternative_explanations=["could be backfill"],
                      unknowns=["team size"], recommended_next_action=action, explanation="x"))


def _agent(turns, web=None, **kw):
    client = ScriptedClaude(turns)
    web = web or FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT)})
    return OpportunityInvestigator("exa-key", client=client, exa_transport=web, now=lambda: NOW, **kw), client, web


READ_JOB = _resp(_use("read", 1, url=JOB))


@pytest.mark.unit
def test_baseline_calls_any_hiring_signal_relevant(context):
    a = baseline_assessment(_account(), _decision(), context, now=NOW)
    assert a.assessment_status == "evidence_backed" and a.recommended_next_action == "consider_persona_enrichment"
    assert a.observed_events[0].check == "page_not_read"  # the baseline never reads a page


@pytest.mark.unit
def test_baseline_without_signals_is_not_a_negative_verdict(context):
    a = baseline_assessment(_account(signal=False), _decision(), context, now=NOW)
    assert a.assessment_status == "insufficient_evidence" and a.recommended_next_action == "no_relevant_evidence"


@pytest.mark.unit
def test_supported_pain_from_a_page_read_a_known_signal_page(context):
    agent, _, web = _agent([READ_JOB, _submit(2, _obs("rebuilding sales onboarding to cut ramp time"))])
    a = agent.assess(_account(), _decision(), context)
    assert a.assessment_status == "evidence_backed" and a.coverage == "found"
    assert a.recommended_next_action == "consider_persona_enrichment"
    assert web.calls == [EXA_CONTENTS_URL]  # the signal page was read without a new search
    assert {r.role_id for r in a.buying_roles} == {"enablement-owner", "revops-stakeholder"}
    assert all("hypothetical" in r.uncertainty.lower() for r in a.buying_roles)
    assert a.icp_decision == "qualified" and a.alternative_explanations == ["could be backfill"]


@pytest.mark.unit
def test_a_hiring_quote_without_the_pain_terms_is_not_support(context):
    agent, _, _ = _agent([READ_JOB, _submit(2, _obs("is hiring five Sales Development Representatives"))])
    a = agent.assess(_account(), _decision(), context)
    assert a.observed_events[0].stance == "unresolved"
    # A checked observation exists but does not establish the pain: look further, conclude nothing.
    assert a.assessment_status == "hypothesis_only" and a.recommended_next_action == "research_more"


@pytest.mark.unit
def test_quote_from_an_unread_page_is_dropped(context):
    agent, _, _ = _agent([_submit(1, _obs("rebuilding sales onboarding to cut ramp time"))])
    a = agent.assess(_account(), _decision(), context)
    assert a.observed_events[0].check == "page_not_read" and a.unsupported_claims == 1
    assert a.assessment_status != "evidence_backed"


@pytest.mark.unit
def test_a_date_the_quote_does_not_state_is_rejected(context):
    agent, _, _ = _agent([READ_JOB, _submit(2, _obs("rebuilding sales onboarding to cut ramp time", date="March 2026"))])
    a = agent.assess(_account(), _decision(), context)
    assert a.observed_events[0].check == "date_not_in_quote"


@pytest.mark.unit
def test_support_and_contradiction_mean_research_more(context):
    old = "https://jobs.example/acme/old"
    web = FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT),
                   old: ("Acme Analytics careers", None, "Acme Analytics: this SDR posting is evergreen and always open; "
                         "onboarding is handled by our existing enablement platform.")})
    turns = [_resp(_use("search", 1, query="acme sdr", purpose="recent_events")), READ_JOB,
             _resp(_use("read", 3, url=old)),
             _submit(4, _obs("rebuilding sales onboarding to cut ramp time"),
                     _obs("onboarding is handled by our existing enablement platform", stance="contradicts", url=old))]
    agent, _, _ = _agent(turns, web)
    a = agent.assess(_account(), _decision(), context)
    assert a.assessment_status == "needs_research" and a.recommended_next_action == "research_more"


@pytest.mark.unit
def test_undecided_icp_is_never_promoted(context):
    agent, _, _ = _agent([READ_JOB, _submit(2, _obs("rebuilding sales onboarding to cut ramp time"))])
    a = agent.assess(_account(), _decision("insufficient_evidence"), context)
    assert a.icp_decision == "insufficient_evidence"
    assert a.recommended_next_action == "review"  # not persona enrichment


@pytest.mark.unit
def test_stopped_investigation_concludes_nothing(context):
    agent, _, _ = _agent([READ_JOB] * 3, max_steps=2)
    a = agent.assess(_account(), _decision(), context)
    assert a.assessment_status == "insufficient_evidence" and a.coverage == "incomplete"
    assert a.recommended_next_action == "research_more"


@pytest.mark.unit
def test_workflow_assesses_new_qualified_accounts_before_enrichment(make_workflow, state_dir):
    from gtm_lab.observability.events import EventRecorder

    turns = [_submit(1, action="defer")] * 30
    agent, _, _ = _agent(turns)
    wf = make_workflow()
    wf.recorder = EventRecorder(state_dir / "events.jsonl")
    wf.opportunity = agent
    plan = wf.plan(limit=50, run_id="opp")
    stored = wf.store.load_assessments(plan.run_id)
    agent_rows = [a for a in stored if a.assessed_by == "agent"]
    assert 0 < len(agent_rows) <= wf.context.research_policy.opportunity_max_accounts
    assert {a.assessed_by for a in stored} == {"agent", "baseline"}
    decisions = {d.account_id: d.decision for d in wf.store.load_decisions(plan.run_id)}
    assert all(decisions[a.account_id] == a.icp_decision for a in stored)  # ICP untouched

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

    def __init__(self, pages, search_returns=None):
        self.pages, self.calls = pages, []  # url -> (title, published, text)
        self.search_returns = search_returns  # urls a search returns (default: all pages)

    def __call__(self, method, url, headers, body):
        req = json.loads(body)
        self.calls.append(url)
        if url == EXA_SEARCH_URL:
            return 200, {"results": [{"url": u, "title": t, "publishedDate": d, "highlights": [x[:80]]}
                                     for u, (t, d, x) in self.pages.items()
                                     if self.search_returns is None or u in self.search_returns],
                         "costDollars": {"total": 0.004}}
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
            "url": url, "quote": quote, "event_date_stated": date, "stance": stance, "pain_id": pain,
            "rationale": "the quote ties hiring to onboarding"}


EVENTS_QUERY = "acme analytics hiring freeze layoffs"


def _check(pain="onboarding-at-scale", query=EVENTS_QUERY, outcome="no_contradiction_found", ids=()):
    return {"pain_id": pain, "alternative": "hiring freeze", "query": query, "outcome": outcome,
            "observation_ids": list(ids)}


def _submit(i, *obs, checks=None, action="consider_persona_enrichment"):
    checks = [_check()] if checks is None else checks
    return _resp(_use("submit", i, observations=list(obs), disconfirmation_checks=checks,
                      alternative_explanations=["could be backfill"], unknowns=["team size"],
                      recommended_next_action=action, explanation="x"))


def _verdict(v="expresses"):
    return _resp(_use("verdict", 99, verdict=v, reason="scripted"))


def _agent(turns, web=None, **kw):
    client = ScriptedClaude(turns)
    web = web or FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT)})
    return OpportunityInvestigator("exa-key", client=client, exa_transport=web, now=lambda: NOW, **kw), client, web


READ_JOB = _resp(_use("read", 1, url=JOB))
SEARCH_EVENTS = _resp(_use("search", 5, query=EVENTS_QUERY, purpose="recent_events"))
SUPPORT = _obs("rebuilding sales onboarding to cut ramp time")


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
def test_verified_support_and_credited_disconfirmation_give_evidence_backed(context):
    agent, client, web = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict()])
    a = agent.assess(_account(), _decision(), context)
    assert a.assessment_status == "evidence_backed" and a.coverage == "found"
    assert a.recommended_next_action == "consider_persona_enrichment"
    assert web.calls == [EXA_CONTENTS_URL, EXA_SEARCH_URL]  # signal page read without searching for it
    m = a.semantic_matches[0]
    assert m.validation == "verified" and m.lexical_term_hit and m.rationale
    c = a.disconfirmation_checks[0]
    assert c.credited and c.pages_read == [JOB] and "read" in c.reason
    assert {r.role_id for r in a.buying_roles} == {"enablement-owner", "revops-stakeholder"}
    assert a.llm_calls == 4  # three turns and one independent verification
    verifier_req = client.requests[-1]
    assert verifier_req["tools"][0]["name"] == "verdict" and JOB_TEXT not in str(verifier_req["messages"])


@pytest.mark.unit
def test_a_paraphrase_without_the_keywords_can_be_support(context):
    # The held-out miss: meaning without the listed terms. The verifier, not the keyword list, decides.
    quote = "new reps learn from whoever has time"
    text = JOB_TEXT + " Today new reps learn from whoever has time."
    web = FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", text)})
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, _obs(quote)), _verdict()], web)
    a = agent.assess(_account(), _decision(), context)
    assert a.semantic_matches[0].lexical_term_hit is False and a.semantic_matches[0].validation == "verified"
    assert a.assessment_status == "evidence_backed"


@pytest.mark.unit
@pytest.mark.parametrize("verdict", ["unrelated", "negates", "unclear"])
def test_keyword_stuffing_or_negation_rejected_by_the_verifier(context, verdict):
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict(verdict)])
    a = agent.assess(_account(), _decision(), context)
    assert a.semantic_matches[0].validation == "rejected_by_verifier"
    assert a.assessment_status != "evidence_backed"


@pytest.mark.unit
def test_verifier_failure_fails_closed(context):
    def boom(_kw):
        raise RuntimeError("down")
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), boom])
    a = agent.assess(_account(), _decision(), context)
    assert a.semantic_matches[0].validation == "unverified" and a.assessment_status != "evidence_backed"


@pytest.mark.unit
def test_quote_from_an_unread_page_is_dropped(context):
    agent, _, _ = _agent([SEARCH_EVENTS, _submit(1, SUPPORT)])
    a = agent.assess(_account(), _decision(), context)
    assert a.observed_events[0].check == "page_not_read" and a.unsupported_claims == 1
    assert a.semantic_matches[0].validation == "ungrounded" and a.assessment_status != "evidence_backed"


@pytest.mark.unit
def test_a_date_the_quote_does_not_state_is_rejected(context):
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS,
                          _submit(2, _obs("rebuilding sales onboarding to cut ramp time", date="March 2026"))])
    a = agent.assess(_account(), _decision(), context)
    assert a.observed_events[0].check == "date_not_in_quote"


@pytest.mark.unit
@pytest.mark.parametrize("query,why", [
    ("sales onboarding software market news", "not a recent_events search naming the company"),
    ("acme analytics something never searched", "no search with this query was run"),
])
def test_a_superficial_or_invented_disconfirmation_is_not_credited(context, query, why):
    search = _resp(_use("search", 2, query=query, purpose="recent_events")) if "never" not in query else SEARCH_EVENTS
    turns = [READ_JOB, search, _submit(3, SUPPORT, checks=[_check(query=query)]), _verdict()]
    agent, _, _ = _agent(turns, max_steps=4)
    a = agent.assess(_account(), _decision(), context)
    assert not a.disconfirmation_checks[0].credited and why in a.disconfirmation_checks[0].reason
    assert a.assessment_status == "needs_research"


@pytest.mark.unit
def test_results_returned_but_not_read_do_not_clear_the_alternative(context):
    other = "https://news.example/acme-update"
    web = FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT),
                   other: ("Acme Analytics update", "2026-11-20T00:00:00.000Z", "Acme Analytics froze hiring.")},
                  search_returns=[other])
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict()], web, max_steps=3)
    a = agent.assess(_account(), _decision(), context)
    c = a.disconfirmation_checks[0]
    assert c.results == [other] and c.pages_read == [] and not c.credited
    assert "none was read" in c.reason and a.assessment_status == "needs_research"


@pytest.mark.unit
def test_rereading_the_support_page_does_not_examine_other_results(context):
    other = "https://news.example/acme-update"
    web = FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT),
                   other: ("Acme Analytics update", "2026-11-20T00:00:00.000Z", "Acme Analytics froze hiring.")})
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict()], web, max_steps=3)
    a = agent.assess(_account(), _decision(), context)
    c = a.disconfirmation_checks[0]
    assert c.pages_read == [JOB] and not c.credited and "may contradict" in c.reason


@pytest.mark.unit
def test_an_empty_search_is_not_found_not_proof(context):
    web = FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT)}, search_returns=[])
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict()], web)
    a = agent.assess(_account(), _decision(), context)
    c = a.disconfirmation_checks[0]
    assert c.credited and "not proof" in c.reason


@pytest.mark.unit
def test_a_credited_contradiction_blocks_evidence_backed(context):
    old = "https://jobs.example/acme/old"
    web = FakeWeb({JOB: ("SDR - Acme Analytics", "2026-11-17T00:00:00.000Z", JOB_TEXT),
                   old: ("Acme Analytics careers", None, "Acme Analytics froze all sales hiring this month.")})
    turns = [READ_JOB, SEARCH_EVENTS, _resp(_use("read", 3, url=old)),
             _submit(4, SUPPORT, _obs("froze all sales hiring this month", stance="contradicts", url=old),
                     checks=[_check(outcome="found_contradiction", ids=["obs-2"])]), _verdict()]
    agent, _, _ = _agent(turns, web)
    a = agent.assess(_account(), _decision(), context)
    assert a.disconfirmation_checks[0].credited
    assert a.assessment_status == "needs_research" and a.recommended_next_action == "research_more"


@pytest.mark.unit
def test_support_without_a_disconfirmation_search_is_sent_back_once(context):
    turns = [READ_JOB, _submit(2, SUPPORT, checks=[]), SEARCH_EVENTS, _submit(4, SUPPORT), _verdict()]
    agent, client, web = _agent(turns)
    a = agent.assess(_account(), _decision(), context)
    sent = client.requests[2]["messages"][-1]["content"][0]
    assert sent["is_error"] and "recent_events" in sent["content"]
    assert a.assessment_status == "evidence_backed" and EXA_SEARCH_URL in web.calls


@pytest.mark.unit
def test_support_without_any_disconfirmation_search_cannot_be_evidence_backed(context):
    turns = [READ_JOB, _submit(2, SUPPORT, checks=[]), _verdict()]
    agent, _, _ = _agent(turns, max_steps=3)  # no room to search and resubmit
    a = agent.assess(_account(), _decision(), context)
    assert a.assessment_status == "needs_research" and a.recommended_next_action == "research_more"
    assert "no credited disconfirmation" in a.next_action_reason


@pytest.mark.unit
def test_undecided_icp_is_never_promoted(context):
    agent, _, _ = _agent([READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict()])
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


@pytest.mark.unit
def test_brief_and_cli_show_persisted_assessments(make_workflow, state_dir):
    from typer.testing import CliRunner

    from gtm_lab.cli import app
    from gtm_lab.opportunity_brief import render_brief

    turns = [READ_JOB, SEARCH_EVENTS, _submit(3, SUPPORT), _verdict()] * 10
    agent, _, _ = _agent(turns)
    wf = make_workflow()
    wf.opportunity = agent
    acc = _account()

    class OneAccount:
        name = "one"

        def discover(self, *, limit):
            from gtm_lab.providers.base import ProviderResult
            return ProviderResult(accounts=[acc.model_copy(deep=True)])

    wf.discovery = OneAccount()
    plan = wf.plan(limit=1, run_id="brief")
    md = render_brief(plan.run_id, wf.store)
    assert "Baseline (signal kind only, no AI)" in md and "`evidence_backed`" in md
    assert '"rebuilding sales onboarding to cut ramp time"' in md and "no person identified" in md
    res = CliRunner().invoke(app, ["opportunity", plan.run_id, "--json", "--state-dir", str(state_dir)])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["accounts"][0]["agent"]["assessment_status"] == "evidence_backed"
    assert data["accounts"][0]["baseline"]["assessed_by"] == "baseline"

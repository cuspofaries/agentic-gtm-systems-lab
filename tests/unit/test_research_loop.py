"""The bounded research loop, with a scripted fake Claude client and a fake Exa."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from gtm_lab.agents.research import EXA_CONTENTS_URL, ResearchQualifier
from gtm_lab.models import Account, SourceAttribution
from gtm_lab.providers.signals import EXA_SEARCH_URL

NOW = datetime(2026, 11, 22, tzinfo=UTC)
PAGE_URL = "https://news.example/acme-headcount"
PAGE_TEXT = "Acme Analytics, a SaaS company based in Austin, Texas, now employs 140 people across sales and product."


def _use(name, i, **inp):
    return SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=inp)


def _resp(*blocks, stop="tool_use", inp=1000, out=200):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


class ScriptedClaude:
    """Returns the scripted turns in order and keeps every request."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})  # the loop appends later
        if not self.turns:
            raise AssertionError("no scripted turn left")
        t = self.turns.pop(0)
        return t(kw) if callable(t) else t


class FakeExa:
    def __init__(self, results=None, page_text=PAGE_TEXT):
        self.results = results if results is not None else [
            {"url": PAGE_URL, "title": "Acme Analytics grows", "publishedDate": "2026-11-01T00:00:00.000Z",
             "highlights": ["Acme Analytics now employs 140 people"]}]
        self.page_text, self.calls = page_text, []

    def __call__(self, method, url, headers, body):
        assert headers["x-api-key"] == "exa-key"
        req = json.loads(body)
        self.calls.append((url, req))
        if url == EXA_SEARCH_URL:
            return 200, {"results": self.results, "costDollars": {"total": 0.004}}
        assert url == EXA_CONTENTS_URL
        return 200, {"results": [{"url": req["urls"][0], "text": self.page_text}], "costDollars": {"total": 0.001}}


def _account():
    return Account(account_id="A1", domain="acme.example", name="Acme Analytics", industry="saas",
                   hq_country="US", employees=None, fields_missing=["employees"],
                   source=SourceAttribution(provider="test"))


def _agent(turns, exa=None, **kw):
    client = ScriptedClaude(turns)
    agent = ResearchQualifier("exa-key", client=client, exa_transport=exa or FakeExa(), now=lambda: NOW, **kw)
    return agent, client


def _claim(quote="now employs 140 people", value="140", url=PAGE_URL, field="employees"):
    return {"field": field, "value": value, "subject": "Acme Analytics", "url": url, "quote": quote}


def _submit(i, *claims, assessment="qualified"):
    return _resp(_use("submit", i, assessment=assessment, explanation="x", claims=list(claims)))


SEARCH = _resp(_use("search", 1, query="Acme Analytics number of employees", purpose="company_facts"))
READ = _resp(_use("read", 2, url=PAGE_URL))
GOOD = [SEARCH, READ, _submit(3, _claim())]


@pytest.mark.unit
def test_claims_checked_reconciled_then_the_rules_decide(context):
    exa = FakeExa()
    agent, client = _agent(GOOD, exa)
    d = agent.qualify(_account(), context)
    assert d.decision == "qualified" and d.decided_by == "hybrid"
    assert [c.check for c in d.claims] == ["quote_found"] and d.claims[0].page_ref == "page-1"
    assert [(r.field, r.status, r.value) for r in d.resolutions] == [("employees", "filled", "140")]
    assert any("employees 140" in e for e in d.evidence)  # the rules' own evidence, on the filled copy
    assert [s.get("tool") for s in agent.last_trace] == ["search", "read", "submit"]
    assert agent.exa_spent_millidollars == 5 and agent.llm_calls == 3
    # Company facts search about two years back, not the 90-day signal window.
    assert exa.calls[0][1]["startPublishedDate"] == "2024-11-22T00:00:00.000Z"
    tool_result = client.requests[2]["messages"][-1]["content"][0]["content"]
    assert tool_result.startswith('<page id="page-1"') and PAGE_TEXT in tool_result


@pytest.mark.unit
def test_recent_events_use_the_signal_window(context):
    exa = FakeExa()
    turns = [_resp(_use("search", 1, query="x", purpose="recent_events")), _submit(2, assessment="insufficient_evidence")]
    agent, _ = _agent(turns, exa)
    agent.qualify(_account(), context)
    assert exa.calls[0][1]["startPublishedDate"] == "2026-08-24T00:00:00.000Z"


@pytest.mark.unit
def test_the_model_does_not_decide(context):
    # Claude says "qualified" but its only claim puts the company outside the band.
    turns = [SEARCH, READ, _submit(3, _claim(quote="now employs 140 people", value="1400"))]
    agent, _ = _agent(turns)
    d = agent.qualify(_account(), context)
    assert d.decision == "not_qualified"
    assert agent.last_trace[-1]["submitted"] == "qualified"


@pytest.mark.unit
def test_disagreeing_sources_stay_open(context):
    page2 = "https://other.example/acme"
    exa = FakeExa(results=[
        {"url": PAGE_URL, "title": "Acme Analytics grows", "publishedDate": "2026-11-01T00:00:00.000Z", "highlights": []},
        {"url": page2, "title": "Acme Analytics profile", "publishedDate": "2026-10-01T00:00:00.000Z", "highlights": []}],
        page_text="Acme Analytics now employs 140 people. Another source: Acme Analytics has 900 employees.")
    turns = [SEARCH, READ, _resp(_use("read", 3, url=page2)),
             _submit(4, _claim(), _claim(quote="Acme Analytics has 900 employees", value="900", url=page2))]
    agent, _ = _agent(turns, exa)
    d = agent.qualify(_account(), context)
    assert d.decision == "insufficient_evidence"
    assert d.resolutions[0].status == "contradicted" and "disagree" in d.resolutions[0].note


@pytest.mark.unit
def test_provider_data_is_never_overwritten(context):
    acc = _account()
    acc.industry = "B2B SaaS Software"  # provider value; only employees is missing
    turns = [SEARCH, READ, _submit(3, _claim(), _claim(field="industry", value="Consulting"))]
    agent, _ = _agent(turns)
    d = agent.qualify(acc, context)
    assert [r.field for r in d.resolutions] == ["employees"]
    assert d.decision == "qualified"


@pytest.mark.unit
def test_a_quote_not_in_the_page_does_not_count(context):
    turns = [SEARCH, READ, _submit(3, _claim(quote="employs 180 people", value="180"))]
    agent, _ = _agent(turns, max_steps=3)  # last step: no resubmission
    d = agent.qualify(_account(), context)
    assert d.decision == "insufficient_evidence"
    assert d.claims[0].check == "quote_not_found" and d.resolutions[0].status == "unresolved"


@pytest.mark.unit
def test_a_page_about_another_company_does_not_count(context):
    exa = FakeExa(results=[{"url": PAGE_URL, "title": "Acme Holdings results", "publishedDate": None, "highlights": []}],
                  page_text="Acme Holdings now employs 140 people.")
    agent, _ = _agent([SEARCH, READ, _submit(3, _claim())], exa, max_steps=3)
    d = agent.qualify(_account(), context)
    assert d.claims[0].check == "subject_mismatch" and d.decision == "insufficient_evidence"


EXCERPT_SUBMIT = _submit(2, _claim())


@pytest.mark.unit
def test_evidence_from_a_page_never_read_does_not_count(context):
    agent, _ = _agent([SEARCH, EXCERPT_SUBMIT], max_steps=3)  # no room left to read and resubmit
    d = agent.qualify(_account(), context)  # the excerpt says it, but the page was not read
    assert d.decision == "insufficient_evidence" and d.claims[0].check == "page_not_read"
    assert agent.last_trace[-1]["submitted"] == "qualified"


@pytest.mark.unit
def test_unverified_submit_is_sent_back_once_to_read_the_page(context):
    # Measured on the real API: Claude quoted search excerpts without reading the pages.
    turns = [SEARCH, EXCERPT_SUBMIT, _resp(_use("read", 3, url=PAGE_URL)), _submit(4, _claim())]
    agent, client = _agent(turns)
    d = agent.qualify(_account(), context)
    assert d.decision == "qualified" and d.resolutions[0].status == "filled"
    assert [s.get("outcome") for s in agent.last_trace][1] == "sent_back"
    sent_back = client.requests[2]["messages"][-1]["content"][0]
    assert sent_back["is_error"] and "read" in sent_back["content"]


@pytest.mark.unit
def test_tools_are_strict_and_not_forced(context):
    agent, client = _agent(GOOD)
    agent.qualify(_account(), context)
    req = client.requests[0]
    assert all(t["strict"] for t in req["tools"])
    assert "tool_choice" not in req  # forced tool use is rejected by this model


@pytest.mark.unit
def test_only_urls_from_a_search_can_be_read(context):
    exa = FakeExa()
    turns = [_resp(_use("read", 1, url="https://attacker.example/x")), _submit(2, assessment="insufficient_evidence")]
    agent, _ = _agent(turns, exa)
    agent.qualify(_account(), context)
    assert exa.calls == []
    assert agent.last_trace[0]["outcome"] == "refused_not_from_search"


@pytest.mark.unit
def test_step_cap_stops_the_loop(context):
    turns = [_resp(_use("search", i, query=f"q{i}", purpose="company_facts")) for i in range(10)]
    agent, client = _agent(turns, max_steps=3)
    d = agent.qualify(_account(), context)
    assert d.decision == "insufficient_evidence" and d.missing == ["research_step_cap_reached"]
    assert len(client.requests) == 4  # the 4th tool call is refused before running


@pytest.mark.unit
def test_exa_budget_is_checked_before_each_call(context):
    exa = FakeExa()
    turns = [SEARCH, _resp(_use("search", 2, query="b", purpose="company_facts")),
             _submit(3, assessment="insufficient_evidence")]
    agent, _ = _agent(turns, exa, max_exa_millidollars=5)
    agent.qualify(_account(), context)
    assert len(exa.calls) == 1 and agent.exa_spent_millidollars == 4
    assert agent.last_trace[1]["outcome"] == "exa_budget_exceeded"


@pytest.mark.unit
def test_llm_budget_is_checked_before_each_turn(context):
    agent, client = _agent(GOOD, max_llm_cents=1)
    d = agent.qualify(_account(), context)
    assert client.requests == [] and d.missing == ["research_llm_budget_would_exceed"]


@pytest.mark.unit
@pytest.mark.parametrize("stop,reason", [("refusal", "research_llm_refused"),
                                         ("max_tokens", "research_output_truncated"),
                                         ("end_turn", "research_no_decision")])
def test_fails_closed(context, stop, reason):
    agent, _ = _agent([_resp(SimpleNamespace(type="text", text="..."), stop=stop)])
    d = agent.qualify(_account(), context)
    assert d.decision == "insufficient_evidence" and d.missing == [reason]


@pytest.mark.unit
def test_api_error_fails_closed(context):
    def boom(_kw):
        raise RuntimeError("down")
    agent, _ = _agent([boom])
    d = agent.qualify(_account(), context)
    assert d.missing == ["research_llm_error"] and agent.errors


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    ("431", (431, 431)), ("~431 employees", (431, 431)), ("500-1,000", (500, 1000)),
    ("between 500 and 1,000 employees", (500, 1000)), ("1.2k", (1200, 1200)), ("a few", None),
])
def test_headcount_parsing(value, expected):
    from gtm_lab.agents.research import headcount_range
    assert headcount_range(value) == expected


@pytest.mark.unit
def test_policy_accepts_a_field_filled_by_checked_research(make_workflow, state_dir):
    """Before typed claims, a research-qualified account was always policy-blocked: its
    evidence named a field the account flags as missing."""
    from gtm_lab.observability.events import EventRecorder

    turns = [SEARCH, READ, _submit(3, _claim())]
    agent, _ = _agent(turns * 20)
    wf = make_workflow(fallback_qualifier=agent)
    wf.recorder = EventRecorder(state_dir / "events.jsonl")
    acc_id = "A1"
    acc = _account()

    class OneAccount:
        name = "one"

        def discover(self, *, limit):
            from gtm_lab.providers.base import ProviderResult
            return ProviderResult(accounts=[acc.model_copy(deep=True)])

    wf.discovery = OneAccount()
    plan = wf.plan(limit=1, run_id="research-policy")
    entry = next(e for e in plan.entries if e.account_id == acc_id)
    assert entry.crm_action == "create_account" and entry.policy_failures == []
    stored = wf.store.load_accounts(plan.run_id)[0]
    assert stored.employees is None and "employees" in stored.fields_missing  # provider data untouched


@pytest.mark.unit
def test_workflow_records_the_steps_of_each_researched_account(make_workflow, state_dir):
    from gtm_lab.observability.events import EventRecorder

    turns = [_submit(1, assessment="insufficient_evidence")] * 20
    agent, client = _agent(turns)
    wf = make_workflow(fallback_qualifier=agent)
    wf.recorder = EventRecorder(state_dir / "events.jsonl")
    plan = wf.plan(limit=50, run_id="research")
    events = [e for e in wf.recorder.read(plan.run_id) if e["event_type"] == "qualification.fallback"]
    assert events and len(client.requests) == len(events)  # only undecided accounts reach it
    assert all(e["payload"]["steps"][-1]["tool"] == "submit" for e in events)

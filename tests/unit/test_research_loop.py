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


GOOD = [
    _resp(_use("search", 1, query="Acme Analytics number of employees")),
    _resp(_use("read", 2, url=PAGE_URL)),
    _resp(_use("submit", 3, decision="qualified", explanation="140 employees, in the band.",
               evidence=[{"url": PAGE_URL, "quote": "now employs 140 people"}])),
]


@pytest.mark.unit
def test_search_read_submit_with_a_quote_found_in_the_page(context):
    exa = FakeExa()
    agent, client = _agent(GOOD, exa)
    d = agent.qualify(_account(), context)
    assert d.decision == "qualified" and d.decided_by == "llm"
    assert d.evidence == [f'{PAGE_URL}: "now employs 140 people" (quote found in the page read)']
    assert [s.get("tool") for s in agent.last_trace] == ["search", "read", "submit"]
    assert agent.exa_spent_millidollars == 5 and agent.llm_calls == 3
    # Searches stay inside the freshness window; page text reaches Claude as tagged data.
    assert exa.calls[0][1]["startPublishedDate"] == "2026-08-24T00:00:00.000Z"
    tool_result = client.requests[2]["messages"][-1]["content"][0]["content"]
    assert tool_result.startswith("<page url=") and PAGE_TEXT in tool_result


@pytest.mark.unit
def test_tools_are_strict_and_not_forced(context):
    agent, client = _agent(GOOD)
    agent.qualify(_account(), context)
    req = client.requests[0]
    assert all(t["strict"] for t in req["tools"])
    assert "tool_choice" not in req  # forced tool use is rejected by this model


@pytest.mark.unit
def test_a_quote_not_in_the_page_does_not_count(context):
    turns = [*GOOD[:2], _resp(_use("submit", 3, decision="qualified", explanation="x",
                                   evidence=[{"url": PAGE_URL, "quote": "employs 180 people"}]))]
    agent, _ = _agent(turns, max_steps=3)  # last step: no resubmission
    d = agent.qualify(_account(), context)
    assert d.decision == "insufficient_evidence"
    assert "research_no_verified_evidence" in d.missing and d.evidence == []


EXCERPT_SUBMIT = _resp(_use("submit", 2, decision="qualified", explanation="x",
                            evidence=[{"url": PAGE_URL, "quote": "now employs 140 people"}]))


@pytest.mark.unit
def test_evidence_from_a_page_never_read_does_not_count(context):
    agent, _ = _agent([GOOD[0], EXCERPT_SUBMIT], max_steps=3)  # no room left to read and resubmit
    d = agent.qualify(_account(), context)  # the excerpt says it, but the page was not read
    assert d.decision == "insufficient_evidence"
    assert agent.last_trace[-1]["submitted"] == "qualified"


@pytest.mark.unit
def test_unverified_submit_is_sent_back_once_to_read_the_page(context):
    # Measured on the real API: Claude quoted search excerpts without reading the pages.
    turns = [GOOD[0], EXCERPT_SUBMIT, _resp(_use("read", 3, url=PAGE_URL)),
             _resp(_use("submit", 4, decision="qualified", explanation="x",
                        evidence=[{"url": PAGE_URL, "quote": "now employs 140 people"}]))]
    agent, client = _agent(turns)
    d = agent.qualify(_account(), context)
    assert d.decision == "qualified" and len(d.evidence) == 1
    assert [s.get("outcome") for s in agent.last_trace][1] == "rejected_unverified_evidence"
    sent_back = client.requests[2]["messages"][-1]["content"][0]
    assert sent_back["is_error"] and "read" in sent_back["content"]


@pytest.mark.unit
def test_only_urls_from_a_search_can_be_read(context):
    exa = FakeExa()
    turns = [_resp(_use("read", 1, url="https://attacker.example/x")),
             _resp(_use("submit", 2, decision="insufficient_evidence", explanation="x", evidence=[]))]
    agent, _ = _agent(turns, exa)
    agent.qualify(_account(), context)
    assert exa.calls == []
    assert agent.last_trace[0]["outcome"] == "refused_not_from_search"


@pytest.mark.unit
def test_step_cap_stops_the_loop(context):
    turns = [_resp(_use("search", i, query=f"q{i}")) for i in range(10)]
    agent, client = _agent(turns, max_steps=3)
    d = agent.qualify(_account(), context)
    assert d.decision == "insufficient_evidence" and d.missing == ["research_step_cap_reached"]
    assert len(client.requests) == 4  # the 4th tool call is refused before running


@pytest.mark.unit
def test_exa_budget_is_checked_before_each_call(context):
    exa = FakeExa()
    turns = [_resp(_use("search", 1, query="a")), _resp(_use("search", 2, query="b")),
             _resp(_use("submit", 3, decision="insufficient_evidence", explanation="x", evidence=[]))]
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
def test_workflow_records_the_steps_of_each_researched_account(make_workflow, state_dir):
    from gtm_lab.observability.events import EventRecorder

    turns = [_resp(_use("submit", 1, decision="insufficient_evidence", explanation="x", evidence=[]))] * 20
    agent, client = _agent(turns)
    wf = make_workflow(fallback_qualifier=agent)
    wf.recorder = EventRecorder(state_dir / "events.jsonl")
    plan = wf.plan(limit=50, run_id="research")
    events = [e for e in wf.recorder.read(plan.run_id) if e["event_type"] == "qualification.fallback"]
    assert events and len(client.requests) == len(events)  # only undecided accounts reach it
    assert all(e["payload"]["steps"][-1]["tool"] == "submit" for e in events)

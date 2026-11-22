"""Agentic path: routing, guards and fail-closed behavior. No real API call is made."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gtm_lab.agents.anthropic_provider import AnthropicProviderCall
from gtm_lab.agents.qualifier import LLMQualifier


class FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.outcome


def _client(stop_reason="end_turn", parsed=None, input_tokens=2000, output_tokens=300):
    resp = SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )
    return SimpleNamespace(messages=FakeMessages(resp))


def _parsed(**over):
    from gtm_lab.agents.anthropic_provider import _LLMDecision

    base = dict(
        decision="qualified",
        explanation="Fits.",
        evidence=[{"field": "industry", "value": "B2B SaaS Software"}, {"field": "hq_country", "value": "US"}],
        missing=["employees"],
        confidence=0.6,
    )
    base.update(over)
    return _LLMDecision(**base)


@pytest.mark.unit
def test_fallback_only_sees_insufficient_evidence_cases(make_workflow):
    seen = []

    def provider(account_json, _ctx):
        seen.append(account_json)
        return {"decision": "not_qualified", "explanation": "x",
                "evidence": ["employees: unknown"], "missing": [], "confidence": 0.5}

    wf = make_workflow(fallback_qualifier=LLMQualifier(provider_call=provider))
    plan = wf.plan(limit=50)
    assert len(seen) == 1 and '"acc_005"' in seen[0]
    events = [e for e in wf.recorder.read(plan.run_id) if e["event_type"] == "qualification.fallback"]
    # The model proposed not_qualified with no checked disqualifying fact: recorded, not applied.
    assert events[0]["payload"] == {
        "account_id": "acc_005",
        "baseline_decision": "insufficient_evidence",
        "fallback_decision": "insufficient_evidence",
        "fallback_decided_by": "llm",
        "agrees_with_baseline": True,
    }
    d = next(d for d in wf.store.load_decisions(plan.run_id) if d.account_id == "acc_005")
    assert "llm_proposal_not_applied:not_qualified" in d.missing


@pytest.mark.unit
def test_llm_evidence_not_in_the_account_is_rejected(make_workflow):
    """A 'qualified' backed by invented or free-text evidence must not reach the plan."""

    def provider(_a, _c):
        return {"decision": "qualified", "explanation": "trust me",
                "evidence": ["great culture fit",                         # free text
                             {"field": "employees", "value": "150"},       # acc_005 has none
                             {"field": "industry", "value": "fintech"}],   # not what it says
                "missing": [], "confidence": 0.99}

    wf = make_workflow(fallback_qualifier=LLMQualifier(provider_call=provider))
    plan = wf.plan(limit=50)
    d = next(d for d in wf.store.load_decisions(plan.run_id) if d.account_id == "acc_005")
    assert d.decision == "insufficient_evidence"
    assert d.missing == ["llm_evidence_ungrounded"]
    assert all(e.account_id != "acc_005" for e in plan.entries)


@pytest.mark.unit
def test_llm_evidence_matching_the_account_is_kept(make_workflow):
    def provider(_a, _c):
        return {"decision": "qualified", "explanation": "US SaaS, size unknown",
                "evidence": [{"field": "hq_country", "value": "US"},
                             {"field": "industry", "value": "b2b saas"},
                             {"field": "employees", "value": "120"}],  # invented: dropped
                "missing": ["employees"], "confidence": 0.6}

    wf = make_workflow(fallback_qualifier=LLMQualifier(provider_call=provider))
    plan = wf.plan(limit=50)
    d = next(d for d in wf.store.load_decisions(plan.run_id) if d.account_id == "acc_005")
    # A grounded 'qualified' is still only a proposal: no new fact was checked, the rules
    # already found these fields insufficient, so the decision does not move.
    assert d.decision == "insufficient_evidence" and d.decided_by == "llm"
    assert "llm_proposal_not_applied:qualified" in d.missing
    assert d.evidence == ["hq_country: US (checked against account data)",
                          "industry: b2b saas (checked against account data)"]
    assert "llm_evidence_partly_ungrounded" in d.missing


@pytest.mark.unit
def test_provider_success_and_cost_tracking():
    p = AnthropicProviderCall(client=_client(parsed=_parsed()))
    out = p("{}", {"icp": {}})
    assert out["decision"] == "qualified"
    assert p.calls == 1
    # 2000 * $4/M + 300 * $20/M = $0.014 -> rounded up to 2 cents
    assert p.spent_cents == 2
    kwargs = p.client.messages.calls[0]
    assert kwargs["model"] == "claude-opus-5-5"
    assert "<account_data>" in kwargs["messages"][0]["content"]


@pytest.mark.unit
def test_provider_call_cap_is_enforced_in_code():
    p = AnthropicProviderCall(client=_client(parsed=_parsed()), max_calls=1)
    p("{}", {})
    out = p("{}", {})
    assert out["decision"] == "insufficient_evidence"
    assert out["missing"] == ["llm_call_cap_reached"]
    assert len(p.client.messages.calls) == 1


@pytest.mark.unit
def test_provider_refuses_a_call_whose_worst_case_exceeds_the_cap():
    p = AnthropicProviderCall(client=_client(parsed=_parsed()), max_spend_cents=3)
    out = p("{}", {})
    assert out["missing"] == ["llm_budget_would_exceed"]
    assert p.client.messages.calls == []  # refused before any request
    assert p.worst_case_cents(1000) > 3


@pytest.mark.unit
def test_spend_never_exceeds_the_cap_across_calls():
    p = AnthropicProviderCall(client=_client(parsed=_parsed()), max_spend_cents=20, max_calls=50)
    for _ in range(50):
        p("{}", {})
    assert p.spent_cents <= 20
    assert p.spent_cents + p.worst_case_cents(1000) > 20  # stopped because the next could overshoot


@pytest.mark.unit
def test_output_cap_hit_fails_closed():
    p = AnthropicProviderCall(client=_client(stop_reason="max_tokens", parsed=None))
    assert p("{}", {})["missing"] == ["llm_output_truncated"]


@pytest.mark.unit
def test_provider_refusal_fails_closed():
    p = AnthropicProviderCall(client=_client(stop_reason="refusal", parsed=None))
    assert p("{}", {})["missing"] == ["llm_refused"]


@pytest.mark.unit
def test_provider_client_error_fails_closed():
    class Boom:
        def parse(self, **_):
            raise TypeError("Could not resolve authentication method")

    p = AnthropicProviderCall(client=SimpleNamespace(messages=Boom()))
    out = p("{}", {})
    assert out["decision"] == "insufficient_evidence"
    assert out["missing"] == ["llm_client_error"]
    assert p.errors

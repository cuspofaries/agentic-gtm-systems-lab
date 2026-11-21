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
        evidence=["industry: B2B SaaS Software", "hq_country: US"],
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
    assert events[0]["payload"] == {
        "account_id": "acc_005",
        "baseline_decision": "insufficient_evidence",
        "fallback_decision": "not_qualified",
        "fallback_decided_by": "llm",
        "agrees_with_baseline": False,
    }


@pytest.mark.unit
def test_llm_qualified_entry_still_goes_through_policies(make_workflow):
    """An LLM 'qualified' with ungrounded evidence must not become approvable."""

    def provider(_a, _c):
        return {"decision": "qualified", "explanation": "trust me",
                "evidence": ["great culture fit", "founder is friendly"], "missing": [],
                "confidence": 0.99}

    wf = make_workflow(fallback_qualifier=LLMQualifier(provider_call=provider))
    plan = wf.plan(limit=50)
    entry = next(e for e in plan.entries if e.account_id == "acc_005")
    assert entry.policy_failures
    assert entry not in plan.approvable_entries


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
def test_provider_spend_cap_is_enforced_in_code():
    p = AnthropicProviderCall(
        client=_client(parsed=_parsed(), input_tokens=1_000_000), max_spend_cents=3
    )
    p("{}", {})  # costs $4 -> 400 cents, over the cap
    out = p("{}", {})
    assert out["missing"] == ["llm_budget_exhausted"]


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

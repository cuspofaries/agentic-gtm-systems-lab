from datetime import UTC, datetime, timedelta

import pytest

from gtm_lab.agents.qualifier import DeterministicQualifier, LLMQualifier
from gtm_lab.models import Account, Confidence, Signal, SourceAttribution


def _acc(**overrides) -> Account:
    base = dict(
        account_id="acc_x",
        domain="example.com",
        name="Example Co",
        employees=85,
        industry="B2B SaaS Software",
        hq_country="US",
        hq_state="CA",
        source=SourceAttribution(provider="test"),
        signals=[],
        employees_confidence=Confidence.VERIFIED,
    )
    base.update(overrides)
    return Account(**base)


@pytest.mark.unit
def test_rules_qualify_clear_yes(context):
    q = DeterministicQualifier()
    d = q.qualify(_acc(), context)
    assert d.decision == "qualified"
    assert d.confidence >= 0.7


@pytest.mark.unit
def test_rules_not_qualified_wrong_industry(context):
    q = DeterministicQualifier()
    d = q.qualify(_acc(industry="Marketing Agency"), context)
    assert d.decision == "not_qualified"


@pytest.mark.unit
def test_rules_not_qualified_wrong_country(context):
    q = DeterministicQualifier()
    d = q.qualify(_acc(hq_country="FR"), context)
    assert d.decision == "not_qualified"


@pytest.mark.unit
def test_rules_insufficient_when_employees_unknown(context):
    q = DeterministicQualifier()
    d = q.qualify(
        _acc(employees=None, employees_confidence=Confidence.UNKNOWN,
             fields_missing=["employees"]),
        context,
    )
    assert d.decision == "insufficient_evidence"


@pytest.mark.unit
def test_rules_signal_within_window_boosts_confidence(context):
    q = DeterministicQualifier()
    now = datetime.now(UTC)
    sig = Signal(
        kind="new_sales_leadership",
        description="VP Sales hired",
        event_date=now - timedelta(days=5),
        source=SourceAttribution(provider="test"),
    )
    d_with = q.qualify(_acc(signals=[sig]), context)
    d_without = q.qualify(_acc(), context)
    assert d_with.decision == "qualified"
    assert d_with.confidence > d_without.confidence


@pytest.mark.unit
def test_rules_signal_outside_window_not_used(context):
    q = DeterministicQualifier()
    now = datetime.now(UTC)
    old = Signal(
        kind="new_sales_leadership",
        description="Old event",
        event_date=now - timedelta(days=200),
        source=SourceAttribution(provider="test"),
    )
    d = q.qualify(_acc(signals=[old]), context)
    assert d.decision == "qualified"
    # Evidence must not mention the signal (it was stale)
    assert not any("signal" in e.lower() for e in d.evidence)


@pytest.mark.unit
def test_llm_qualifier_unconfigured_is_safe(context):
    q = LLMQualifier()  # no provider
    d = q.qualify(_acc(), context)
    assert d.decision == "insufficient_evidence"
    assert d.confidence == 0.0


@pytest.mark.unit
def test_llm_qualifier_rejects_evidence_less_qualified(context):
    def bogus(_a, _ctx):
        return {"decision": "qualified", "evidence": [], "confidence": 0.99}

    q = LLMQualifier(provider_call=bogus)
    d = q.qualify(_acc(), context)
    assert d.decision == "insufficient_evidence"  # evidence floor enforced on LLM output

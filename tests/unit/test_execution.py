"""Execution metadata: what ran, per component, from what was executed rather than configured."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from gtm_lab.agents.opportunity import OpportunityInvestigator
from gtm_lab.agents.qualifier import LLMQualifier
from gtm_lab.agents.research import ResearchQualifier
from gtm_lab.observability.events import EventRecorder
from gtm_lab.summary import run_summary
from tests.unit.test_exa_signals import _StaticSignals
from tests.unit.test_opportunity import FakeWeb, _submit
from tests.unit.test_research_loop import ScriptedClaude


class CountingProvider:
    """Stands in for AnthropicProviderCall: counts calls and spend like the real one."""

    model = "claude-opus-5-5"

    def __init__(self, verdict="qualified"):
        self.calls, self.spent_cents, self.errors, self.verdict = 0, 0, [], verdict

    def __call__(self, account_json, ctx):
        self.calls += 1
        self.spent_cents += 3
        return {"decision": self.verdict, "explanation": "x", "evidence": [], "missing": [], "confidence": 0.5}


def _run(make_workflow, state_dir, limit=50, **attrs):
    wf = make_workflow(fallback_qualifier=attrs.pop("fallback", None))
    wf.recorder = EventRecorder(state_dir / "events.jsonl")
    for k, v in attrs.items():
        setattr(wf, k, v)
    plan = wf.plan(limit=limit)
    return wf, plan, run_summary(plan.run_id, state_dir)


@pytest.mark.unit
def test_fully_offline_run(make_workflow, state_dir):
    _, _, s = _run(make_workflow, state_dir)
    c = s["execution"]["components"]
    assert s["mode"] == "offline" and s["llm_calls_made"] == 0 and s["execution"]["offline"]
    assert c["contact_enrichment"]["kind"] == "simulated" and c["crm"]["kind"] == "simulated"
    assert not any(c[k]["configured"] for k in ("llm_qualifier", "company_research", "opportunity_intelligence"))


@pytest.mark.unit
def test_llm_qualifier_configured_but_never_called(make_workflow, state_dir):
    provider = CountingProvider()
    _, _, s = _run(make_workflow, state_dir, limit=4, fallback=LLMQualifier(provider_call=provider))
    c = s["execution"]["components"]["llm_qualifier"]
    assert c["configured"] and not c["executed"] and c["llm_calls"] == 0 and provider.calls == 0
    assert s["mode"] == "offline"  # configured is not executed


@pytest.mark.unit
def test_llm_qualifier_actually_called(make_workflow, state_dir):
    _, _, s = _run(make_workflow, state_dir, fallback=LLMQualifier(provider_call=CountingProvider()))
    c = s["execution"]["components"]["llm_qualifier"]
    assert c["executed"] and c["llm_calls"] == 1 and c["llm_cost_cents"] == 3 and c["model"] == "claude-opus-5-5"
    assert s["mode"] == "agentic" and s["llm_calls_made"] == 1


def _investigator(turns, **kw):
    web = FakeWeb({"https://jobs.example/acme/sdr": ("SDR", None, "x")})
    return OpportunityInvestigator("exa-key", client=ScriptedClaude(turns), exa_transport=web, **kw)


@pytest.mark.unit
def test_opportunity_without_llm_qualification_is_not_offline(make_workflow, state_dir):
    """The reported bug: --opportunity exa without --agentic was summarized as offline, 0 calls."""
    inv = _investigator([_submit(1, checks=[], action="defer")] * 20)
    _, _, s = _run(make_workflow, state_dir, opportunity=inv)
    c = s["execution"]["components"]
    assert not c["llm_qualifier"]["configured"]
    assert c["opportunity_intelligence"]["executed"] and c["opportunity_intelligence"]["llm_calls"] >= 1
    assert s["mode"] == "agentic" and s["llm_calls_made"] == c["opportunity_intelligence"]["llm_calls"]


@pytest.mark.unit
def test_research_attempted_but_failed(make_workflow, state_dir):
    def boom(_kw):
        raise RuntimeError("down")
    agent = ResearchQualifier("exa-key", client=ScriptedClaude([boom] * 5))
    wf, plan, s = _run(make_workflow, state_dir, fallback=agent)
    c = s["execution"]["components"]["company_research"]
    assert c["configured"] and c["accounts"] == 1 and c["llm_calls"] == 0 and c["errors"] == 1
    d = next(d for d in wf.store.load_decisions(plan.run_id) if d.decided_by == "hybrid")
    assert d.decision == "insufficient_evidence" and "research_llm_error" in d.missing


@pytest.mark.unit
def test_budget_exhausted_before_any_call(make_workflow, state_dir, context):
    agent = ResearchQualifier("exa-key", client=ScriptedClaude([]), max_llm_cents=0)
    ctx = context.model_copy(deep=True)
    ctx.signals.budget_millidollars = 0
    wf = make_workflow(ctx, fallback_qualifier=agent)
    wf.recorder = EventRecorder(state_dir / "events.jsonl")
    wf.signals = _StaticSignals()
    plan = wf.plan(limit=50)
    c = run_summary(plan.run_id, state_dir)["execution"]["components"]
    assert c["company_research"]["llm_calls"] == 0 and wf.signals.calls == 0
    assert c["signal_discovery"]["calls"] == 0 and c["signal_discovery"]["skipped_budget_exhausted"] > 0


@pytest.mark.unit
def test_mixed_components_keep_their_units_apart(make_workflow, state_dir):
    inv = _investigator([_submit(1, checks=[], action="defer")] * 20)
    _, plan, s = _run(make_workflow, state_dir, opportunity=inv, signals=_StaticSignals())
    e = s["execution"]
    sig = e["components"]["signal_discovery"]
    assert sig["executed"] and sig["kind"] == "live" and sig["cost_millidollars"] == 4 * sig["calls"]
    assert e["exa_cost_millidollars_total"] == sig["cost_millidollars"] + e["components"]["opportunity_intelligence"][
        "exa_cost_millidollars"]
    assert e["contact_cost_cents_total"] == plan.enrichment_spent_cents
    assert "signal_discovery" in e["live_components"]


@pytest.mark.unit
def test_replayed_answers_are_not_reported_as_live(make_workflow, state_dir):
    inv = _investigator([_submit(1, checks=[], action="defer")] * 20)
    inv.execution_kind = "replayed"
    _, _, s = _run(make_workflow, state_dir, opportunity=inv)
    assert s["execution"]["components"]["opportunity_intelligence"]["kind"] == "replayed"
    assert s["mode"] == "agentic_replayed"


@pytest.mark.unit
def test_summary_is_the_same_from_another_process(make_workflow, state_dir):
    inv = _investigator([_submit(1, checks=[], action="defer")] * 20)
    _, plan, s = _run(make_workflow, state_dir, opportunity=inv)
    code = (f"import json; from pathlib import Path; from gtm_lab.summary import run_summary; "
            f"print(json.dumps(run_summary({plan.run_id!r}, Path({str(state_dir)!r}))['execution']))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == s["execution"]


@pytest.mark.unit
def test_legacy_runs_without_metadata_are_flagged(state_dir, make_workflow):
    wf = make_workflow()
    rec = EventRecorder(state_dir / "events.jsonl")
    wf.recorder = rec
    plan = wf.plan(limit=50)
    lines = [json.loads(x) for x in (state_dir / "events.jsonl").read_text().splitlines()]
    (state_dir / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines
                                                    if x["event_type"] != "execution.summary"))
    s = run_summary(plan.run_id, state_dir)
    assert s["execution"]["legacy"] and s["mode"] == "offline"


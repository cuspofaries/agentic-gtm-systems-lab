"""The opportunity evaluation harness: cases load, a recording replays identically
offline, and the committed cassette (if present) replays without a miss."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_lab.context import load_context
from gtm_lab.evaluations.opportunity_eval import LLMCassette, _naive, metrics, run_eval

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals" / "opportunity" / "cases.yaml"
CASSETTE = ROOT / "evals" / "opportunity" / "cassette.json"


@pytest.fixture
def ctx():
    return load_context(ROOT / "examples" / "account-discovery-to-crm" / "config" / "gtm-context.yaml")


@pytest.mark.integration
def test_record_then_replay_gives_the_same_metrics(ctx, tmp_path):
    tape = LLMCassette(tmp_path / "c.json", "record", _naive(ctx))
    recorded = metrics(run_eval(CASES, ctx, tape))
    tape.save("naive")
    run = run_eval(CASES, ctx, LLMCassette(tmp_path / "c.json", "replay"))
    assert run.misses == [] and metrics(run) == recorded
    assert recorded["agent"]["cases"] >= 20


@pytest.mark.integration
def test_the_baseline_calls_most_labeled_non_relevant_cases_relevant(ctx, tmp_path):
    """The point of comparison: a signal-kind rule cannot tell a relevant hiring signal from
    an evergreen posting, a support role or a namesake."""
    tape = LLMCassette(tmp_path / "c.json", "record", _naive(ctx))
    b = metrics(run_eval(CASES, ctx, tape))["baseline"]
    assert b["false_positive"] > b["true_negative"]


DECISIONS = ROOT / "evals" / "decisions" / "cases.yaml"
DECISIONS_CASSETTE = ROOT / "evals" / "decisions" / "cassette.json"


@pytest.mark.integration
@pytest.mark.skipif(not DECISIONS_CASSETTE.exists(), reason="no recorded decisions cassette yet")
def test_decisions_cassette_replays_and_the_code_keeps_control(ctx):
    from gtm_lab.evaluations.decision_eval import strategies

    run = run_eval(DECISIONS, ctx, LLMCassette(DECISIONS_CASSETTE, "replay"))
    assert run.misses == [] and len(run.results) == 16
    for r in run.results:
        a = r.agent
        grounded = {o.obs_id for o in a.observed_events if o.check == "quote_found"}
        for h in a.pain_hypotheses:
            assert set(h.supporting) <= grounded  # nothing supports unless its quote was checked
        verified = {m.observation_id for m in a.semantic_matches if m.validation == "verified"}
        for h in a.pain_hypotheses:
            assert set(h.supporting) <= verified  # nor unless the independent check confirmed it
        if a.assessment_status == "evidence_backed":
            assert any(c.credited and c.outcome_claimed == "no_contradiction_found" for c in a.disconfirmation_checks)
    st = strategies(run, ctx, 80)
    assert st["opportunity_gated"]["enriched"] <= st["all_qualified"]["enriched"]

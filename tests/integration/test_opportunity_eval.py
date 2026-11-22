"""The opportunity evaluation harness: cases load, a recording replays identically
offline, and the committed cassette (if present) replays without a miss."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_lab.context import load_context
from gtm_lab.evaluations.opportunity_eval import LLMCassette, NaiveReader, metrics, run_eval

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals" / "opportunity" / "cases.yaml"
CASSETTE = ROOT / "evals" / "opportunity" / "cassette.json"


@pytest.fixture
def ctx():
    return load_context(ROOT / "examples" / "account-discovery-to-crm" / "config" / "gtm-context.yaml")


@pytest.mark.integration
def test_record_then_replay_gives_the_same_metrics(ctx, tmp_path):
    tape = LLMCassette(tmp_path / "c.json", "record", NaiveReader(ctx.offers[0].pains[0]))
    recorded = metrics(run_eval(CASES, ctx, tape))
    tape.save("naive")
    run = run_eval(CASES, ctx, LLMCassette(tmp_path / "c.json", "replay"))
    assert run.misses == [] and metrics(run) == recorded
    assert recorded["agent"]["cases"] >= 20


@pytest.mark.integration
def test_the_baseline_calls_most_labeled_non_relevant_cases_relevant(ctx, tmp_path):
    """The point of comparison: a signal-kind rule cannot tell a relevant hiring signal from
    an evergreen posting, a support role or a namesake."""
    tape = LLMCassette(tmp_path / "c.json", "record", NaiveReader(ctx.offers[0].pains[0]))
    b = metrics(run_eval(CASES, ctx, tape))["baseline"]
    assert b["false_positive"] > b["true_negative"]


@pytest.mark.integration
@pytest.mark.skipif(not CASSETTE.exists(), reason="no recorded cassette yet")
def test_committed_cassette_replays_without_a_miss(ctx):
    run = run_eval(CASES, ctx, LLMCassette(CASSETTE, "replay"))
    assert run.misses == []
    m = metrics(run)["agent"]
    # Whatever the model said, nothing counts as evidence unless the code checked it.
    for r in run.results:
        for h in r.agent.pain_hypotheses:
            ids = {o.obs_id for o in r.agent.observed_events if o.check == "quote_found"}
            assert set(h.supporting) <= ids and set(h.contradicting) <= ids
    assert m["cases"] == len(run.results)

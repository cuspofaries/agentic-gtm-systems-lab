"""End-to-end offline demo: the regression gate for the bundled fixture.

If these numbers drift, something changed in the pipeline. They match
examples/account-discovery-to-crm/expected-results/summary.md.
"""

from __future__ import annotations

import socket

import pytest

from gtm_lab.evaluations import evaluate_run
from gtm_lab.models import WritePlanStatus


@pytest.mark.integration
def test_end_to_end_plan_then_sync(workflow, store, crm):
    plan = workflow.plan(limit=50)
    assert plan.status == WritePlanStatus.PROPOSED

    decisions = store.load_decisions(plan.run_id)
    assert len(store.load_accounts(plan.run_id)) == 11
    counts = {k: sum(1 for d in decisions if d.decision == k)
              for k in ("qualified", "not_qualified", "insufficient_evidence")}
    assert counts == {"qualified": 9, "not_qualified": 1, "insufficient_evidence": 1}

    assert len(plan.approvable_entries) == 7
    assert plan.enrichment_spent_cents == 50

    # Nothing is written before approval.
    assert len(crm.all_accounts()) == 1

    workflow.approve(plan.run_id)
    results = workflow.sync(plan.run_id)
    by_outcome = {}
    for r in results:
        by_outcome[(r.operation, r.outcome)] = by_outcome.get((r.operation, r.outcome), 0) + 1
    assert by_outcome == {
        ("create_account", "ok"): 7,
        ("create_contact", "ok"): 5,
        ("skip", "idempotent_noop"): 2,
    }
    assert store.load_plan(plan.run_id).status == WritePlanStatus.SYNCED
    assert len(crm.all_accounts()) == 8

    report = evaluate_run(plan.run_id, store)
    assert report.ok, report.failures


@pytest.mark.integration
def test_resync_is_a_reported_noop(workflow, crm):
    plan = workflow.plan(limit=50)
    workflow.approve(plan.run_id)
    workflow.sync(plan.run_id)
    second = workflow.sync(plan.run_id)
    assert {r.outcome for r in second} == {"idempotent_noop"}
    assert len(crm.all_accounts()) == 8


@pytest.mark.integration
def test_offline_workflow_makes_no_network_calls(workflow, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("offline workflow attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    plan = workflow.plan(limit=50)
    workflow.approve(plan.run_id)
    workflow.sync(plan.run_id)

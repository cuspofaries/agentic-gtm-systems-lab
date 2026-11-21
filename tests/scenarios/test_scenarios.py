"""Named failure/success scenarios.

S01-S12 map 1:1 to the brief and to docs/failure-scenarios.md. S13 covers the brief's
"CRM state changed since approval" requirement.
"""

from __future__ import annotations

import pytest

from gtm_lab.models import WritePlanStatus


def _decision(store, run_id, account_id):
    return next(d for d in store.load_decisions(run_id) if d.account_id == account_id)


def _entry(plan, account_id):
    return next(e for e in plan.entries if e.account_id == account_id)


@pytest.mark.scenario
def test_s01_icp_match_qualifies(workflow, store):
    plan = workflow.plan(limit=50)
    assert _decision(store, plan.run_id, "acc_001").decision == "qualified"
    assert _entry(plan, "acc_001") in plan.approvable_entries


@pytest.mark.scenario
def test_s02_non_icp_rejected(workflow, store):
    plan = workflow.plan(limit=50)
    d = _decision(store, plan.run_id, "acc_002")
    assert d.decision == "not_qualified"
    assert any("consulting-agencies" in e for e in d.evidence)
    assert all(e.account_id != "acc_002" for e in plan.entries)


@pytest.mark.scenario
def test_s03_already_in_crm_skipped(workflow):
    plan = workflow.plan(limit=50)
    entry = _entry(plan, "acc_003")
    assert entry.skipped
    assert entry.skip_reason == "Already exists in CRM"
    assert entry.enrichment_status == "not_needed"  # no money spent on a known account


@pytest.mark.scenario
def test_s04_duplicate_in_batch_marked_ambiguous(workflow):
    plan = workflow.plan(limit=50)
    entry = _entry(plan, "acc_004_dup")
    assert entry.skipped
    assert "Ambiguous" in entry.skip_reason


@pytest.mark.scenario
def test_s05_missing_employee_data_yields_insufficient_evidence(workflow, store):
    plan = workflow.plan(limit=50)
    d = _decision(store, plan.run_id, "acc_005")
    assert d.decision == "insufficient_evidence"
    assert "employees unknown" in d.missing


@pytest.mark.scenario
def test_s06_stale_signal_is_not_counted_as_evidence(workflow, store):
    plan = workflow.plan(limit=50)
    d = _decision(store, plan.run_id, "acc_006")
    assert d.decision == "qualified"
    assert not any("signal" in e.lower() for e in d.evidence)


@pytest.mark.scenario
def test_s07_weak_evidence_not_qualified(make_workflow, context):
    """Raise the evidence floor to 4: only accounts with a fresh signal (acc_001, acc_003) clear it."""
    strict = context.model_copy(
        update={"policy": context.policy.model_copy(update={"min_evidence_count": 4})}
    )
    wf = make_workflow(ctx=strict)
    plan = wf.plan(limit=50)
    decisions = {d.account_id: d for d in wf.store.load_decisions(plan.run_id)}
    assert decisions["acc_001"].decision == "qualified"
    assert decisions["acc_007"].decision == "insufficient_evidence"  # 3 facts, no signal
    qualified = {k for k, d in decisions.items() if d.decision == "qualified"}
    assert qualified == {"acc_001", "acc_003"}
    assert [e.account_id for e in plan.approvable_entries] == ["acc_001"]  # acc_003 is in CRM


@pytest.mark.scenario
def test_s08_incomplete_contact_handled_gracefully(workflow, crm):
    plan = workflow.plan(limit=50)
    entry = _entry(plan, "acc_008")
    assert [c.email for c in entry.contacts_to_create] == [None]
    workflow.approve(plan.run_id)
    workflow.sync(plan.run_id)
    acc = crm.find_accounts_by_domain("incomplete.saas.example")[0]
    contacts = crm.find_contacts_by_account(acc.crm_account_id)
    assert len(contacts) == 1 and contacts[0].email is None


@pytest.mark.scenario
def test_s09_enrichment_budget_enforced(workflow):
    plan = workflow.plan(limit=50)
    assert plan.enrichment_spent_cents == plan.enrichment_budget_cents == 50
    for account_id in ("acc_009", "acc_010"):
        e = _entry(plan, account_id)
        assert e.enrichment_status == "budget_exceeded"
        assert e.contacts_to_create == []


@pytest.mark.scenario
def test_s10_transient_crm_write_failure_is_retried(workflow, store):
    plan = workflow.plan(limit=50)
    workflow.approve(plan.run_id)
    workflow.crm.inject_failure("transient CRM error", times=1)
    results = workflow.sync(plan.run_id)
    assert not [r for r in results if r.outcome == "failed"]
    first = next(r for r in results if r.operation == "create_account")
    assert first.attempt == 2
    assert store.load_plan(plan.run_id).status == WritePlanStatus.SYNCED


@pytest.mark.scenario
def test_s11_interrupted_sync_resumes_in_new_process(make_workflow):
    wf1 = make_workflow()
    plan = wf1.plan(limit=50)
    wf1.approve(plan.run_id)
    # First account create fails on every attempt (1 + 2 retries): the sync ends PARTIAL.
    wf1.crm.inject_failure("CRM down", times=3)
    first = wf1.sync(plan.run_id)
    assert [r.account_id for r in first if r.outcome == "failed"] == ["acc_001"]
    assert wf1.store.load_plan(plan.run_id).status == WritePlanStatus.PARTIAL

    # A fresh process picks the run up from disk and finishes it.
    wf2 = make_workflow()
    second = wf2.sync(plan.run_id)
    by_acc = {
        r.account_id: r.outcome for r in second if r.operation == "create_account"
    }
    assert by_acc["acc_001"] == "ok"
    assert all(v == "idempotent_noop" for k, v in by_acc.items() if k != "acc_001")
    assert wf2.store.load_plan(plan.run_id).status == WritePlanStatus.SYNCED
    assert len(wf2.crm.all_accounts()) == 8


@pytest.mark.scenario
def test_s12_human_rejection_prevents_sync(workflow, crm):
    plan = workflow.plan(limit=50)
    workflow.reject(plan.run_id, reason="not now")
    with pytest.raises(ValueError):
        workflow.sync(plan.run_id)
    assert len(crm.all_accounts()) == 1  # only the seed


@pytest.mark.scenario
def test_s13_crm_changed_after_approval_is_not_overwritten(workflow, crm, store):
    plan = workflow.plan(limit=50)
    workflow.approve(plan.run_id)
    # Another writer creates Brightpath between approval and sync.
    other = crm.create_account(
        domain="brightpath.saas.example", name="Brightpath (sales rep)",
        source="manual", external_ref="manual:brightpath",
    )
    results = workflow.sync(plan.run_id)
    acc_001 = [r for r in results if r.account_id == "acc_001"]
    assert [r.outcome for r in acc_001] == ["stale_plan"]  # no contact writes either
    assert crm.find_contacts_by_account(other.crm_account_id) == []
    assert len(crm.find_accounts_by_domain("brightpath.saas.example")) == 1
    assert store.load_plan(plan.run_id).status == WritePlanStatus.PARTIAL

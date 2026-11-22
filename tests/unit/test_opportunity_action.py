"""Opportunity action policy and opportunity-gated enrichment: every branch of the action
matrix, fail-closed defaults, persona mapping, and the actual provider calls made."""

from __future__ import annotations

import pytest

from gtm_lab.models import (
    OpportunityAssessment,
    QualificationDecision,
    RoleHypothesis,
)
from gtm_lab.policies.opportunity_action import decide
from gtm_lab.providers.base import EnrichmentProvider, ProviderResult


def _dec(d="qualified", aid="A"):
    return QualificationDecision(account_id=aid, decision=d, explanation="", confidence=0.8)


def _assess(status="evidence_backed", coverage="found", roles=("enablement-owner",), unsupported=0,
            by="agent", aid="A"):
    return OpportunityAssessment(
        account_id=aid, offer_id="sales-onboarding", icp_decision="qualified", icp_decided_by="rules",
        assessed_by=by, assessment_status=status, recommended_next_action="review", next_action_reason="",
        coverage=coverage, unsupported_claims=unsupported,
        buying_roles=[RoleHypothesis(role_id=r, role="problem_owner", title_hints=[], reason="x") for r in roles])


@pytest.mark.unit
@pytest.mark.parametrize("status,coverage,action", [
    ("evidence_backed", "found", "propose_enrichment"),
    ("not_relevant", "found", "defer"),
    ("needs_research", "found", "research_more"),
    ("hypothesis_only", "found", "research_more"),
    ("insufficient_evidence", "checked_no_result", "review"),
    ("insufficient_evidence", "incomplete", "research_more"),
    ("insufficient_evidence", "failed", "research_more"),
    ("evidence_backed", "incomplete", "review"),
])
def test_action_matrix(context, status, coverage, action):
    assert decide(_dec(), _assess(status, coverage), context)[0] == action


@pytest.mark.unit
def test_not_assessed_is_review_not_irrelevant(context):
    act, why, personas, _ = decide(_dec(), None, context)
    assert act == "review" and "not a finding that the account is not relevant" in why and personas == []
    assert decide(_dec(), _assess(by="baseline"), context)[0] == "review"  # the baseline never unlocks spend


@pytest.mark.unit
def test_icp_not_qualified_is_never_promoted(context):
    assert decide(_dec("insufficient_evidence"), _assess(), context)[0] == "review"


@pytest.mark.unit
def test_dropped_claims_send_to_review(context):
    assert decide(_dec(), _assess(unsupported=1), context)[0] == "review"


@pytest.mark.unit
def test_personas_come_from_the_explicit_role_mapping(context):
    act, _, personas, reasons = decide(_dec(), _assess(roles=("enablement-owner", "sales-sponsor")), context)
    assert act == "propose_enrichment"
    assert personas == ["Sales Enablement", "VP Sales", "Head of Sales"]
    assert "not linked" in reasons["Revenue Operations"]


@pytest.mark.unit
def test_an_unmapped_role_blocks_instead_of_enriching_everyone(context):
    ctx = context.model_copy(deep=True)
    next(r for r in ctx.buying_roles if r.id == "sales-sponsor").personas = []
    act, why, personas, _ = decide(_dec(), _assess(roles=("enablement-owner", "sales-sponsor")), ctx)
    assert act == "review" and "sales-sponsor" in why and personas == []


@pytest.mark.unit
def test_unknown_persona_in_mapping_is_rejected_at_load(context):
    data = context.model_dump()
    data["buying_roles"][0]["personas"] = ["Chief Vibes Officer"]
    with pytest.raises(ValueError, match="persona"):
        type(context).model_validate(data)


# --- workflow -------------------------------------------------------------------------


class CountingEnrichment(EnrichmentProvider):
    name = "counting"
    cost_cents_per_call = 10

    def __init__(self):
        self.calls = []

    def enrich(self, account, personas):
        self.calls.append((account.account_id, list(personas)))
        return ProviderResult(contacts=[], cost_cents=10)


class ScriptedInvestigator:
    """Returns a fixed assessment per account id; no network, no LLM."""

    def __init__(self, by_account: dict):
        self.by_account = by_account

    def assess(self, account, decision, ctx):
        status = self.by_account.get(account.account_id, "not_relevant")
        roles = ("enablement-owner",) if status == "evidence_backed" else ()
        return _assess(status=status, roles=roles, aid=account.account_id)


def _workflow(make_workflow, statuses, strategy, scope="new"):
    wf = make_workflow()
    wf.enrichment = CountingEnrichment()
    wf.opportunity = ScriptedInvestigator(statuses) if statuses is not None else None
    wf.enrichment_strategy = strategy
    wf.opportunity_scope = scope
    return wf


@pytest.mark.unit
def test_default_strategy_is_unchanged(make_workflow):
    wf = _workflow(make_workflow, None, "all_qualified")
    plan = wf.plan(limit=50, run_id="default")
    enriched = [e for e in plan.entries if e.enrichment_status == "done"]
    assert len(wf.enrichment.calls) == len(enriched) > 0
    assert all(e.opportunity_action is None for e in plan.entries)


@pytest.mark.unit
def test_gated_mode_calls_the_provider_only_for_evidence_backed_accounts(make_workflow):
    wf0 = _workflow(make_workflow, None, "all_qualified")
    candidates = [e.account_id for e in wf0.plan(limit=50, run_id="probe").entries
                  if e.enrichment_status in ("done", "budget_exceeded")]
    first = candidates[0]
    wf = _workflow(make_workflow, {first: "evidence_backed"}, "opportunity_gated")
    wf.context.research_policy.opportunity_max_accounts = 50
    plan = wf.plan(limit=50, run_id="gated")
    assert wf.enrichment.calls == [(first, ["Sales Enablement"])]  # mapped persona only
    deferred = [e for e in plan.entries if e.enrichment_status == "deferred_by_policy"]
    assert deferred and all(e.skipped and e.opportunity_action.provider_calls_avoided == 1 for e in deferred)
    assert all(e not in plan.approvable_entries for e in deferred)  # no CRM write proposed for them
    rec = next(e for e in plan.entries if e.account_id == first).opportunity_action
    assert rec.applied and rec.action == "propose_enrichment" and rec.actual_cost_cents == 10


@pytest.mark.unit
def test_gated_mode_without_assessments_spends_nothing(make_workflow):
    wf = _workflow(make_workflow, None, "opportunity_gated")
    plan = wf.plan(limit=50, run_id="nothing")
    assert wf.enrichment.calls == []
    assert {e.opportunity_action.assessment_status for e in plan.entries if e.opportunity_action} == {"not_assessed"}


@pytest.mark.unit
def test_cap_reached_accounts_are_not_assessed_and_not_enriched(make_workflow):
    wf = _workflow(make_workflow, {}, "opportunity_gated")
    wf.opportunity = ScriptedInvestigator({})  # everything assessed would be not_relevant
    wf.context.research_policy.opportunity_max_accounts = 1
    plan = wf.plan(limit=50, run_id="cap")
    states = [e.opportunity_action.assessment_status for e in plan.entries if e.opportunity_action]
    assert "not_assessed" in states and wf.enrichment.calls == []


@pytest.mark.unit
def test_all_qualified_with_assessments_records_a_shadow_estimate(make_workflow):
    wf = _workflow(make_workflow, {}, "all_qualified")
    wf.context.research_policy.opportunity_max_accounts = 50
    plan = wf.plan(limit=50, run_id="shadow")
    recs = [e.opportunity_action for e in plan.entries if e.opportunity_action]
    assert recs and all(not r.applied for r in recs) and wf.enrichment.calls  # spend happened anyway
    assert {r.action for r in recs} == {"defer"}


@pytest.mark.unit
def test_existing_crm_accounts_are_assessed_read_only_with_scope_all(make_workflow):
    wf = _workflow(make_workflow, {}, "all_qualified", scope="all")
    wf.context.research_policy.opportunity_max_accounts = 50
    plan = wf.plan(limit=50, run_id="scope-all")
    existing = {e.account_id for e in plan.entries if e.crm_action == "skip_existing" and e.crm_account_id}
    assessed = {a.account_id for a in wf.store.load_assessments(plan.run_id) if a.assessed_by == "agent"}
    assert existing and existing <= assessed
    assert all(e.skipped for e in plan.entries if e.account_id in existing)  # still no write

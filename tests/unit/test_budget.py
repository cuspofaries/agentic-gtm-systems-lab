import pytest

from gtm_lab.workflows import EnrichmentBudget


@pytest.mark.unit
def test_budget_allows_up_to_cap():
    b = EnrichmentBudget(cap_cents=30)
    assert b.can_spend(10)
    b.charge(10)
    b.charge(10)
    b.charge(10)
    assert b.spent_cents == 30
    assert b.remaining == 0


@pytest.mark.unit
def test_budget_refuses_over_cap():
    b = EnrichmentBudget(cap_cents=10)
    b.charge(10)
    assert not b.can_spend(1)
    with pytest.raises(ValueError):
        b.charge(1)


@pytest.mark.unit
def test_budget_remaining_never_negative():
    b = EnrichmentBudget(cap_cents=5, spent_cents=10)  # artificially over
    assert b.remaining == 0


class _Overcharging:
    """An enrichment provider that reports more than the maximum it announced."""

    name = "overcharger"
    cost_cents_per_call = 10
    max_cost_cents_per_call = 10

    def enrich(self, account, personas):
        from gtm_lab.providers.base import ProviderResult
        return ProviderResult(contacts=[], cost_cents=25)


@pytest.mark.unit
def test_an_overcharge_is_booked_and_flagged_not_clamped(make_workflow):
    from gtm_lab.evaluations.checks import evaluate_run

    wf = make_workflow()
    wf.enrichment = _Overcharging()
    plan = wf.plan(limit=50)
    enriched = [e for e in plan.entries if e.enrichment_status == "done"]
    assert enriched and all(any("overcharge" in f for f in e.policy_failures) for e in enriched)
    assert plan.enrichment_spent_cents == 25 * len(enriched)
    assert not evaluate_run(plan.run_id, wf.store).ok or plan.enrichment_spent_cents <= plan.enrichment_budget_cents


@pytest.mark.unit
def test_two_plans_in_the_same_second_get_different_run_ids(make_workflow):
    a, b = make_workflow().plan(limit=50), make_workflow().plan(limit=50)
    assert a.run_id != b.run_id

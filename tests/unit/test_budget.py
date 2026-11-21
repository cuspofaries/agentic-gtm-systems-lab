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

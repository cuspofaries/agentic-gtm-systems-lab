from pathlib import Path

import pytest

from gtm_lab.crm.base import CRMError
from gtm_lab.crm.mock import MockCRM
from gtm_lab.models import ContactProposal, SourceAttribution


@pytest.mark.unit
def test_create_account_idempotent(tmp_path: Path):
    crm = MockCRM(tmp_path / "crm.sqlite")
    a1 = crm.create_account(
        domain="example.com", name="Example", source="run_1", external_ref="ref_a"
    )
    a2 = crm.create_account(
        domain="example.com", name="Example renamed", source="run_1", external_ref="ref_a"
    )
    assert a1.crm_account_id == a2.crm_account_id
    assert len(crm.all_accounts()) == 1


@pytest.mark.unit
def test_create_contact_requires_valid_parent(tmp_path: Path):
    crm = MockCRM(tmp_path / "crm.sqlite")
    p = ContactProposal(
        account_id="acc_x",
        persona="VP Sales",
        first_name="A",
        last_name="B",
        title="VP Sales",
        email="a@example.invalid",
        source=SourceAttribution(provider="test"),
    )
    with pytest.raises(CRMError):
        crm.create_contact(p, crm_account_id="does_not_exist", external_ref="r_c")


@pytest.mark.unit
def test_inject_failure_retriable(tmp_path: Path):
    crm = MockCRM(tmp_path / "crm.sqlite")
    crm.inject_failure("boom", times=1)
    with pytest.raises(CRMError) as exc:
        crm.create_account(
            domain="example.com", name="Ex", source="s", external_ref="x"
        )
    assert exc.value.retriable is True
    # Next call succeeds
    crm.create_account(domain="example.com", name="Ex", source="s", external_ref="x")

from pathlib import Path

import pytest

from gtm_lab.crm.mock import MockCRM
from gtm_lab.models import Account, SourceAttribution
from gtm_lab.workflows import Reconciliation


def _mk_account(**over):
    base = dict(
        account_id="acc_x",
        domain="example.com",
        name="Ex",
        employees=50,
        industry="SaaS",
        hq_country="US",
        source=SourceAttribution(provider="test"),
    )
    base.update(over)
    return Account(**base)


@pytest.mark.unit
def test_reconciliation_no_hit(tmp_path: Path):
    crm = MockCRM(tmp_path / "crm.sqlite")
    recon = Reconciliation()
    matches = recon.reconcile([_mk_account()], crm)
    assert matches[0].match_type == "none"


@pytest.mark.unit
def test_reconciliation_exact_hit(tmp_path: Path):
    crm = MockCRM(tmp_path / "crm.sqlite")
    crm.create_account(domain="example.com", name="Ex", source="seed", external_ref="x")
    recon = Reconciliation()
    matches = recon.reconcile([_mk_account()], crm)
    assert matches[0].match_type == "exact_domain"


@pytest.mark.unit
def test_reconciliation_canonicalizes_input(tmp_path: Path):
    crm = MockCRM(tmp_path / "crm.sqlite")
    crm.create_account(domain="example.com", name="Ex", source="seed", external_ref="x")
    recon = Reconciliation()
    weird = _mk_account(domain="HTTPS://www.Example.COM/foo")
    matches = recon.reconcile([weird], crm)
    assert matches[0].match_type == "exact_domain"

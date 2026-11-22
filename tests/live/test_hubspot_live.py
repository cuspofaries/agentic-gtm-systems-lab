"""Live test against a real HubSpot portal. Opt-in, never in CI.

    export HUBSPOT_SERVICE_KEY=...      # a TEST portal, never a production CRM
    GTM_LAB_LIVE_HUBSPOT=1 pytest -m live_hubspot

It plans, approves and syncs the example fixture into HubSpot, checks idempotency and a
real stale plan, then deletes every record it created (and nothing else).
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from gtm_lab.agents.qualifier import DeterministicQualifier
from gtm_lab.crm.hubspot import API_VERSION, REF_PROPERTY, HubSpotCRM
from gtm_lab.observability.events import NullRecorder
from gtm_lab.persistence.store import RunStore
from gtm_lab.providers import FixtureDiscoveryProvider, FixtureEnrichmentProvider
from gtm_lab.workflows import AccountDiscoveryWorkflow

TOKEN = os.environ.get("HUBSPOT_SERVICE_KEY") or os.environ.get("HUBSPOT_ACCESS_TOKEN")
pytestmark = [
    pytest.mark.live_hubspot,
    pytest.mark.skipif(
        not (TOKEN and os.environ.get("GTM_LAB_LIVE_HUBSPOT") == "1"),
        reason="set GTM_LAB_LIVE_HUBSPOT=1 and HUBSPOT_SERVICE_KEY to run against a test portal",
    ),
]
SEARCH_LAG_S = 12  # measured ~6.5 s; wait longer to make the stale check deterministic


def _delete_created(crm: HubSpotCRM, run_id: str, extra_company_ids=()):
    for obj in ("contacts", "companies"):
        after = None
        while True:
            page = crm._call(
                "GET",
                f"/crm/objects/{API_VERSION}/{obj}?limit=100&properties={REF_PROPERTY}"
                + (f"&after={after}" if after else ""),
            )
            for r in page.get("results", []):
                if (r["properties"].get(REF_PROPERTY) or "").startswith(f"gtm-lab:{run_id}:"):
                    crm._call("DELETE", f"/crm/objects/{API_VERSION}/{obj}/{r['id']}")
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break
    for cid in extra_company_ids:
        crm._call("DELETE", f"/crm/objects/{API_VERSION}/companies/{cid}", ok404=True)


def test_plan_approve_sync_resync_and_stale_plan_against_hubspot(context, tmp_path):
    from tests.conftest import EXAMPLE_DIR

    crm = HubSpotCRM(TOKEN, contact_legal_basis=context.crm.hubspot_contact_legal_basis)
    crm.ensure_schema()  # the explicit setup step; a no-op on an already prepared portal
    wf = AccountDiscoveryWorkflow(
        context=context,
        discovery=FixtureDiscoveryProvider(EXAMPLE_DIR / "fixtures" / "discovery.yaml"),
        enrichment=FixtureEnrichmentProvider(EXAMPLE_DIR / "fixtures" / "enrichment.yaml"),
        crm=crm,
        store=RunStore(tmp_path / "runs.sqlite"),
        recorder=NullRecorder(),
        qualifier=DeterministicQualifier(),
    )
    run_id = f"live_{uuid.uuid4().hex[:10]}"
    stale_company = None
    # Portals with "create and associate companies with contacts" on create a company for
    # the contacts' email domain (example.com). Remove it afterwards if this test caused it.
    email_domain_existed = bool(crm.find_accounts_by_domain("example.com"))
    try:
        # The fixture domains must not exist yet, or the plan would skip them.
        assert not crm.find_accounts_by_domain("brightpath.saas.example"), "portal not clean"
        plan = wf.plan(limit=50, run_id=run_id)
        assert plan.crm_target == "hubspot"
        wf.approve(run_id)

        # Someone else creates one of the planned companies after approval.
        stale_company = crm._call(
            "POST",
            f"/crm/objects/{API_VERSION}/companies",
            {"properties": {"name": "Created by someone else", "domain": "silentwave.saas.example"}},
        )["id"]
        time.sleep(SEARCH_LAG_S)

        first = wf.sync(run_id)
        by = {(r.account_id, r.operation): r.outcome for r in first}
        assert by[("acc_007", "create_account")] == "stale_plan"
        assert ("acc_007", "create_contact") not in by
        assert by[("acc_001", "create_account")] == "ok"
        assert not [r for r in first if r.outcome == "failed"], [r.error for r in first if r.error]
        assert wf.store.load_plan(run_id).status.value == "partial"

        account = crm.find_account_by_external_ref(f"gtm-lab:{run_id}:acc_001")
        contacts = crm.find_contacts_by_account(account.crm_account_id)
        assert sorted(c.last_name for c in contacts) == ["Hall", "Ortiz"]

        second = wf.sync(run_id)
        assert {r.outcome for r in second if r.account_id != "acc_007"} == {"idempotent_noop"}
    finally:
        extra = [stale_company] if stale_company else []
        if not email_domain_existed:
            time.sleep(SEARCH_LAG_S)
            extra += [a.crm_account_id for a in crm.find_accounts_by_domain("example.com")]
        _delete_created(crm, run_id, extra)

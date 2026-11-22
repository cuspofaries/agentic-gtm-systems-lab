"""HubSpotCRM against an in-memory fake that reproduces behavior measured on a real portal:

- a duplicate value on the unique ref property -> 400 VALIDATION_ERROR naming the property;
- a duplicate domain is accepted (HubSpot does not enforce it);
- a missing idProperty lookup -> 404 with an HTML body;
- contacts need hs_legal_basis when the portal requires it;
- search can lag behind writes.
"""

from __future__ import annotations

import json
import urllib.parse
from types import SimpleNamespace

import pytest

from gtm_lab.crm.base import CRMError
from gtm_lab.crm.hubspot import CONTACT_TO_COMPANY, REF_PROPERTY, HubSpotCRM
from gtm_lab.models import ContactProposal, SourceAttribution

TOKEN = "pat-test-not-a-real-token"


class FakeHubSpot:
    def __init__(self, *, require_legal_basis=False, search_lag=False, schema_ready=True):
        self.companies: dict[str, dict] = {}
        self.contacts: dict[str, dict] = {}
        self.assoc: dict[str, list[str]] = {}
        self.properties: set[tuple[str, str]] = set()
        self.require_legal_basis = require_legal_basis
        self.search_lag = search_lag
        self.calls: list[SimpleNamespace] = []
        self.fail_next: list[int] = []
        self._next = 1000
        self.portal_id = 111
        if schema_ready:
            self.properties = {("companies", REF_PROPERTY), ("contacts", REF_PROPERTY)}

    def __call__(self, method, url, headers, body):
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        parsed = urllib.parse.urlparse(url)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        data = json.loads(body) if body else None
        self.calls.append(SimpleNamespace(method=method, path=path, query=query, body=data))
        if self.fail_next:
            code = self.fail_next.pop(0)
            return code, {"message": "boom", "category": "X"}, {"Retry-After": "0"}
        parts = path.strip("/").split("/")

        if parts[:2] == ["account-info", "v3"]:
            return 200, {"portalId": self.portal_id, "accountType": "STANDARD"}, {}
        if parts[:2] == ["crm", "properties"]:
            obj = parts[3]
            if method == "GET":
                return (200, {"name": parts[4]}, {}) if (obj, parts[4]) in self.properties else (404, "<html>404</html>", {})
            self.properties.add((obj, data["name"]))
            return 201, {"name": data["name"], "hasUniqueValue": data["hasUniqueValue"]}, {}

        if parts[:2] == ["crm", "v4"]:
            ids = self.assoc.get(parts[4], [])
            return 200, {"results": [{"toObjectId": int(i), "associationTypes": [{"typeId": 280}]} for i in ids]}, {}

        obj = parts[3]
        store = self.companies if obj == "companies" else self.contacts
        if method == "POST" and len(parts) == 5 and parts[4] == "search":
            f = data["filterGroups"][0]["filters"][0]
            wanted = {v.lower() for v in (f.get("values") or [f.get("value")])}
            hits = [] if self.search_lag else [
                r for r in store.values() if (r["properties"].get("domain") or "").lower() in wanted
            ]
            return 200, {"total": len(hits), "results": hits}, {}
        if method == "POST" and len(parts) == 4:
            props = data["properties"]
            ref = props.get(REF_PROPERTY)
            if ref and any(r["properties"].get(REF_PROPERTY) == ref for r in store.values()):
                return 400, {"category": "VALIDATION_ERROR", "message": f"Cannot set PropertyValueCoordinates{{propertyName={REF_PROPERTY}, value={ref}}}"}, {}
            if obj == "contacts" and self.require_legal_basis and "hs_legal_basis" not in props:
                return 400, {"category": "VALIDATION_ERROR", "message": "required properties were missing: [hs_legal_basis]"}, {}
            oid = str(self._next)
            self._next += 1
            store[oid] = {"id": oid, "properties": dict(props)}
            for a in data.get("associations", []):
                self.assoc.setdefault(a["to"]["id"], []).append(oid)
            return 201, store[oid], {}
        if method == "GET" and len(parts) == 5:
            key = urllib.parse.unquote(parts[4])
            if query.get("idProperty") == [REF_PROPERTY]:
                hit = next((r for r in store.values() if r["properties"].get(REF_PROPERTY) == key), None)
            else:
                hit = store.get(key)
            return (200, hit, {}) if hit else (404, "<html>Error 404</html>", {})
        if method == "GET" and len(parts) == 4:
            return 200, {"results": list(store.values())}, {}
        raise AssertionError(f"unexpected call {method} {path}")


def _crm(fake, **kw):
    return HubSpotCRM(TOKEN, transport=fake, sleep=lambda _s: None, **kw)


def _contact(email="riley.hall@example.com"):
    return ContactProposal(
        account_id="acc_001", persona="VP Sales", first_name="Riley", last_name="Hall",
        title="VP Sales", email=email, source=SourceAttribution(provider="test"),
    )


@pytest.mark.unit
def test_planning_reads_do_not_touch_the_schema():
    fake = FakeHubSpot(schema_ready=False)
    _crm(fake).find_accounts_by_domain("HTTPS://www.Brightpath.saas.example/")
    assert [c.method for c in fake.calls] == ["POST"]
    assert fake.calls[0].path.endswith("/companies/search")
    f = fake.calls[0].body["filterGroups"][0]["filters"][0]
    assert f["operator"] == "IN" and "brightpath.saas.example" in f["values"]
    assert fake.properties == set()


@pytest.mark.unit
def test_writes_never_create_the_schema():
    fake = FakeHubSpot(schema_ready=False)
    crm = _crm(fake)
    with pytest.raises(CRMError, match="crm-setup") as exc:
        crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    assert exc.value.retriable is False
    assert fake.properties == set() and fake.companies == {}
    assert not [c for c in fake.calls if c.path.startswith("/crm/properties") and c.method == "POST"]


@pytest.mark.unit
def test_explicit_setup_creates_unique_property_once():
    fake = FakeHubSpot(schema_ready=False)
    crm = _crm(fake)
    assert crm.ensure_schema() == ["companies", "contacts"]
    assert crm.ensure_schema() == []
    creates = [c for c in fake.calls if c.path.startswith("/crm/properties") and c.method == "POST"]
    assert len(creates) == 2 and all(c.body["hasUniqueValue"] is True for c in creates)


@pytest.mark.unit
def test_identity_is_the_portal():
    assert _crm(FakeHubSpot()).identity() == "hubspot:111"


@pytest.mark.unit
def test_sync_refuses_a_plan_made_against_another_portal(make_workflow):
    from gtm_lab.models import WritePlanStatus

    wf = make_workflow()
    plan = wf.plan(limit=50)
    wf.approve(plan.run_id)
    stored = wf.store.load_plan(plan.run_id)
    stored.crm_target, stored.crm_identity = "hubspot", "hubspot:111"
    stored.status = WritePlanStatus.APPROVED
    wf.store.save_plan(stored)
    other = FakeHubSpot()
    other.portal_id = 222
    wf.crm = _crm(other)
    with pytest.raises(ValueError, match=r"hubspot:111.*hubspot:222"):
        wf.sync(plan.run_id)
    assert other.companies == {}


@pytest.mark.unit
def test_create_account_is_idempotent_on_ref():
    fake = FakeHubSpot()
    crm = _crm(fake)
    a = crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    b = crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    assert a.crm_account_id == b.crm_account_id
    assert len(fake.companies) == 1


@pytest.mark.unit
def test_ref_conflict_from_a_race_returns_the_existing_record():
    fake = FakeHubSpot()
    crm = _crm(fake)
    fake.companies["9"] = {"id": "9", "properties": {"domain": "zz.example", REF_PROPERTY: "r1"}}
    # Make the pre-check miss it once, as a concurrent writer would, so the POST hits the 400.
    original = crm.find_account_by_external_ref
    calls = {"n": 0}

    def flaky(ref):
        calls["n"] += 1
        return None if calls["n"] == 1 else original(ref)

    crm.find_account_by_external_ref = flaky
    got = crm.create_account(domain="other.example", name="X", source="run", external_ref="r1")
    assert got.crm_account_id == "9"
    assert len(fake.companies) == 1


@pytest.mark.unit
def test_domain_owned_by_another_record_is_refused():
    fake = FakeHubSpot()
    crm = _crm(fake)
    crm.create_account(domain="a.example", name="A", source="seed", external_ref="seed:a")
    with pytest.raises(CRMError) as exc:
        crm.create_account(domain="a.example", name="A again", source="run", external_ref="r2")
    assert exc.value.retriable is False
    assert len(fake.companies) == 1


@pytest.mark.unit
def test_contact_is_associated_and_carries_legal_basis():
    fake = FakeHubSpot(require_legal_basis=True)
    crm = _crm(fake, contact_legal_basis="Not applicable")
    acc = crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    c = crm.create_contact(_contact(), crm_account_id=acc.crm_account_id, external_ref="c1")
    post = [x for x in fake.calls if x.method == "POST" and x.path.endswith("/contacts")][-1]
    assert post.body["properties"]["hs_legal_basis"] == "Not applicable"
    assert post.body["associations"][0]["types"][0]["associationTypeId"] == CONTACT_TO_COMPANY
    assert [x.crm_contact_id for x in crm.find_contacts_by_account(acc.crm_account_id)] == [c.crm_contact_id]


@pytest.mark.unit
def test_contact_without_email_omits_the_field():
    fake = FakeHubSpot()
    crm = _crm(fake)
    acc = crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    crm.create_contact(_contact(email=None), crm_account_id=acc.crm_account_id, external_ref="c1")
    post = [x for x in fake.calls if x.method == "POST" and x.path.endswith("/contacts")][-1]
    assert "email" not in post.body["properties"]


@pytest.mark.unit
def test_missing_legal_basis_gives_an_actionable_error():
    fake = FakeHubSpot(require_legal_basis=True)
    crm = _crm(fake)  # not configured
    acc = crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    with pytest.raises(CRMError) as exc:
        crm.create_contact(_contact(), crm_account_id=acc.crm_account_id, external_ref="c1")
    assert exc.value.retriable is False
    assert "crm.hubspot_contact_legal_basis" in str(exc.value)


@pytest.mark.unit
def test_missing_ref_lookup_returns_none_on_html_404():
    crm = _crm(FakeHubSpot())
    assert crm.find_account_by_external_ref("gtm-lab:run:acc_404") is None
    assert crm.find_contact_by_external_ref("gtm-lab:run:contact:x:VP Sales:A:B") is None


@pytest.mark.unit
@pytest.mark.parametrize("status, retriable", [(429, True), (500, True), (503, True), (400, False), (403, False)])
def test_error_mapping(status, retriable):
    fake = FakeHubSpot()
    fake.fail_next = [status]
    crm = _crm(fake)
    with pytest.raises(CRMError) as exc:
        crm.find_accounts_by_domain("a.example")
    assert exc.value.retriable is retriable
    assert str(status) in str(exc.value)


@pytest.mark.unit
def test_token_never_appears_in_errors():
    fake = FakeHubSpot()
    fake.fail_next = [500]
    with pytest.raises(CRMError) as exc:
        _crm(fake).find_accounts_by_domain("a.example")
    assert TOKEN not in str(exc.value)


@pytest.mark.unit
def test_search_lag_is_a_known_gap_covered_by_the_ref_property():
    """With a lagging search the domain check can miss a record; the unique ref still
    prevents this run from writing the same company twice."""
    fake = FakeHubSpot(search_lag=True)
    crm = _crm(fake)
    crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    crm.create_account(domain="a.example", name="A", source="run", external_ref="r1")
    assert len(fake.companies) == 1


@pytest.mark.unit
def test_workflow_refuses_to_sync_a_plan_to_another_crm(make_workflow, state_dir):
    wf = make_workflow()
    plan = wf.plan(limit=50)
    wf.approve(plan.run_id)
    wf.crm = _crm(FakeHubSpot())
    with pytest.raises(ValueError, match="reconciled against the 'mock' CRM"):
        wf.sync(plan.run_id)


@pytest.mark.unit
def test_revalidation_error_is_recorded_not_raised(make_workflow):
    from gtm_lab.crm.mock import MockCRM

    class FlakyLookups(MockCRM):
        calls = 0

        def find_account_by_external_ref(self, ref):
            FlakyLookups.calls += 1
            if FlakyLookups.calls == 1:
                raise CRMError("HubSpot server error (503)", retriable=True)
            return super().find_account_by_external_ref(ref)

    wf = make_workflow()
    plan = wf.plan(limit=50)
    wf.approve(plan.run_id)
    wf.crm = FlakyLookups(wf.crm.db_path)
    results = wf.sync(plan.run_id)
    first = next(r for r in results if r.operation == "create_account")
    assert first.outcome == "failed" and "revalidate" in first.error
    assert sum(1 for r in results if r.operation == "create_account" and r.outcome == "ok") == 6
    assert wf.store.load_plan(plan.run_id).status.value == "partial"


@pytest.mark.unit
@pytest.mark.parametrize("typed", ["www.acquia.com", "https://acquia.com/", "HTTP://WWW.Acquia.com", "acquia.com"])
def test_domain_typed_differently_in_the_crm_is_still_found(typed):
    """Measured on a real portal: HubSpot keeps the domain as typed by the user."""
    fake = FakeHubSpot()
    fake.companies["7"] = {"id": "7", "properties": {"domain": typed, "name": "Acquia (entered by sales)"}}
    crm = _crm(fake)
    assert [a.crm_account_id for a in crm.find_accounts_by_domain("acquia.com")] == ["7"]
    with pytest.raises(CRMError):
        crm.create_account(domain="acquia.com", name="Acquia", source="run", external_ref="r1")

"""Email finding (FullEnrich) and verification (ZeroBounce) against fake transports that
reproduce documented and measured behavior:

- FullEnrich: POST starts a bulk enrichment, GET returns 400 `error.enrichment.in_progress`
  until it is FINISHED; `most_probable_work_email` carries the email and its status;
  only found emails cost a credit.
- ZeroBounce: statuses as returned by its sandbox addresses; a bad key or empty balance is
  HTTP 200 with an `error` field; `unknown` costs nothing.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from gtm_lab.models import Account, ContactProposal, SourceAttribution
from gtm_lab.providers.base import EnrichmentProvider, ProviderResult
from gtm_lab.providers.email import (
    FULLENRICH_API,
    EmailPipeline,
    FullEnrichFinder,
    ZeroBounceVerifier,
)

FE_KEY, ZB_KEY = "fe-test-key", "zb-test-key"


def _account():
    return Account(account_id="A1", domain="acme.example", name="Acme",
                   source=SourceAttribution(provider="test"))


def _contact(first, last, email=None, url=None, verified=False):
    return ContactProposal(account_id="A1", persona="VP Sales", first_name=first, last_name=last,
                           title="VP Sales", email=email, verified=verified,
                           source=SourceAttribution(provider="test", url=url))


class FakeFullEnrich:
    def __init__(self, emails: dict[str, tuple[str, str]], polls_before_done=1, final="FINISHED",
                 final_http=200, in_progress_as_200=False):
        self.emails = emails  # last_name -> (email, status)
        self.polls_before_done, self.final = polls_before_done, final
        self.final_http, self.in_progress_as_200 = final_http, in_progress_as_200
        self.calls, self.started = [], None

    def __call__(self, method, url, headers, body):
        assert headers["Authorization"] == f"Bearer {FE_KEY}"
        self.calls.append((method, url))
        if method == "POST":
            assert url == FULLENRICH_API + "/contact/enrich/bulk"
            self.started = json.loads(body)
            return 200, {"enrichment_id": "e-1"}
        assert url == FULLENRICH_API + "/contact/enrich/bulk/e-1"
        if self.polls_before_done:
            self.polls_before_done -= 1
            if self.in_progress_as_200:
                return 200, {"id": "e-1", "status": "IN_PROGRESS", "cost": {"credits": 0}, "data": []}
            return 400, {"code": "error.enrichment.in_progress",
                         "message": "Enrichment not ready, try again in 30 seconds"}
        data = []
        for item in self.started["data"]:
            hit = self.emails.get(item["last_name"].lower())
            info = {"most_probable_work_email": {"email": hit[0], "status": hit[1]}} if hit else {}
            data.append({"input": {}, "custom": item["custom"], "contact_info": info})
        found = sum(1 for d in data if d["contact_info"])
        return self.final_http, {"id": "e-1", "status": self.final, "cost": {"credits": found}, "data": data}


class FakeZeroBounce:
    def __init__(self, statuses: dict[str, tuple[str, str]] | None = None, error=None):
        self.statuses, self.error, self.calls = statuses or {}, error, []

    def __call__(self, method, url, headers, body):
        assert method == "POST" and url.endswith("/v2/validate")
        assert ZB_KEY not in url  # the key travels in the body only
        form = dict(urllib.parse.parse_qsl(body.decode(), keep_blank_values=True))
        assert form["api_key"] == ZB_KEY
        self.calls.append(form["email"])
        if self.error:
            return 200, {"error": self.error}
        status, sub = self.statuses.get(form["email"], ("valid", ""))
        return 200, {"address": form["email"], "status": status, "sub_status": sub}


class StaticContacts(EnrichmentProvider):
    name = "static"
    cost_cents_per_call = 56

    def __init__(self, contacts):
        self.contacts = contacts

    def enrich(self, account, personas):
        return ProviderResult(contacts=list(self.contacts), cost_cents=28)


def _finder(fake):
    return FullEnrichFinder(FE_KEY, cents_per_credit=10, transport=fake, sleep=lambda _s: None)


def _verifier(fake):
    return ZeroBounceVerifier(ZB_KEY, cents_per_credit=2, transport=fake)


@pytest.mark.unit
def test_fullenrich_request_asks_for_work_emails_only_and_maps_back():
    fake = FakeFullEnrich({"hall": ("riley.hall@acme.example", "DELIVERABLE")})
    contacts = [_contact("Riley", "Hall", url="https://linkedin.com/in/riley-hall"),
                _contact("Sam", "Ortiz")]
    r = _finder(fake).find(_account(), contacts)
    item = fake.started["data"][0]
    assert item["enrich_fields"] == ["contact.work_emails"]
    assert item["domain"] == "acme.example" and item["linkedin_url"].endswith("riley-hall")
    assert "linkedin_url" not in fake.started["data"][1]
    assert r.found[0].email == "riley.hall@acme.example" and r.found[0].status == "valid"
    assert 1 not in r.found
    assert r.cost_cents == 10  # one credit, only for the email found
    assert [m for m, _ in fake.calls] == ["POST", "GET", "GET"]  # polled until finished


@pytest.mark.unit
def test_fullenrich_skips_contacts_that_already_have_an_email():
    fake = FakeFullEnrich({})
    r = _finder(fake).find(_account(), [_contact("A", "B", email="a@acme.example")])
    assert fake.calls == [] and r.found == {} and r.cost_cents == 0


@pytest.mark.unit
def test_fullenrich_failure_is_reported_not_raised():
    fake = FakeFullEnrich({"hall": ("r@acme.example", "DELIVERABLE")}, final="CREDITS_INSUFFICIENT")
    r = _finder(fake).find(_account(), [_contact("Riley", "Hall")])
    assert r.failures and "CREDITS_INSUFFICIENT" in r.failures[0]
    assert FE_KEY not in r.failures[0]


@pytest.mark.unit
def test_fullenrich_measured_answers_200_in_progress_then_402_out_of_credits():
    fake = FakeFullEnrich({"hall": ("r@acme.example", "DELIVERABLE")}, polls_before_done=2,
                          final="CREDITS_INSUFFICIENT", final_http=402, in_progress_as_200=True)
    r = _finder(fake).find(_account(), [_contact("Riley", "Hall")])
    assert [m for m, _ in fake.calls] == ["POST", "GET", "GET", "GET"]  # waited through IN_PROGRESS
    assert "CREDITS_INSUFFICIENT" in r.failures[0] and "402" in r.failures[0]
    assert r.found[0].email == "r@acme.example"  # what was found before running out is kept


@pytest.mark.unit
def test_fullenrich_gives_up_after_the_timeout():
    fake = FakeFullEnrich({}, polls_before_done=10**6)
    f = FullEnrichFinder(FE_KEY, cents_per_credit=10, transport=fake, sleep=lambda _s: None,
                         first_poll_s=10, poll_every_s=15, timeout_s=40)
    r = f.find(_account(), [_contact("Riley", "Hall")])
    assert r.found == {} and "not finished" in r.failures[0]


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected,cost", [
    ("valid", "valid", 2), ("catch-all", "risky", 2), ("unknown", "unknown", 0),
    ("invalid", "invalid", 2), ("spamtrap", "invalid", 2), ("do_not_mail", "invalid", 2),
])
def test_zerobounce_statuses_are_normalized(raw, expected, cost):
    v = _verifier(FakeZeroBounce({"x@acme.example": (raw, "")})).verify("x@acme.example")
    assert (v.status, v.cost_cents, v.failure) == (expected, cost, None)


@pytest.mark.unit
def test_zerobounce_error_in_a_200_is_a_failure_not_a_verdict():
    v = _verifier(FakeZeroBounce(error="Invalid API key or your account ran out of credits")).verify("x@a.example")
    assert v.failure and "ran out of credits" in v.failure and ZB_KEY not in v.failure
    assert v.cost_cents == 0


@pytest.mark.unit
def test_pipeline_finds_then_verifies_and_the_verifier_has_the_last_word():
    fe = FakeFullEnrich({"hall": ("riley@acme.example", "DELIVERABLE"),
                         "ortiz": ("sam@acme.example", "CATCH_ALL")})
    zb = FakeZeroBounce({"riley@acme.example": ("catch-all", ""), "sam@acme.example": ("valid", "")})
    p = EmailPipeline(StaticContacts([_contact("Riley", "Hall"), _contact("Sam", "Ortiz")]),
                      finder=_finder(fe), verifier=_verifier(zb), max_contacts_per_call=2)
    r = p.enrich(_account(), ["VP Sales"])
    riley, sam = r.contacts
    assert (riley.email, riley.email_status, riley.verified) == ("riley@acme.example", "risky", False)
    assert riley.email_checks == ["fullenrich:DELIVERABLE", "zerobounce:catch-all"]
    assert (sam.email_status, sam.verified) == ("valid", True)
    assert r.cost_cents == 28 + 2 * 10 + 2 * 2
    assert p.max_cost_cents_per_call == 56 + 2 * (10 + 2)
    assert p.name == "static+fullenrich+zerobounce"


@pytest.mark.unit
def test_invalid_email_is_always_removed():
    zb = FakeZeroBounce({"old@acme.example": ("invalid", "mailbox_not_found")})
    p = EmailPipeline(StaticContacts([_contact("A", "B", email="old@acme.example")]),
                      verifier=_verifier(zb), max_contacts_per_call=2)
    c = p.enrich(_account(), ["VP Sales"]).contacts[0]
    assert c.email is None and c.email_status == "invalid" and not c.verified
    assert c.email_checks == ["zerobounce:invalid/mailbox_not_found", "email removed (invalid)"]


@pytest.mark.unit
def test_drop_email_rule_removes_risky_and_unknown():
    zb = FakeZeroBounce({"a@acme.example": ("catch-all", ""), "b@acme.example": ("unknown", "greylisted"),
                         "c@acme.example": ("valid", "")})
    contacts = [_contact("A", "A", email="a@acme.example"), _contact("B", "B", email="b@acme.example"),
                _contact("C", "C", email="c@acme.example")]
    p = EmailPipeline(StaticContacts(contacts), verifier=_verifier(zb), max_contacts_per_call=3,
                      on_unverified="drop_email")
    out = p.enrich(_account(), ["VP Sales"]).contacts
    assert [c.email for c in out] == [None, None, "c@acme.example"]
    assert [c.email_status for c in out] == ["risky", "unknown", "valid"]


@pytest.mark.unit
def test_verifier_outage_keeps_the_finder_verdict_and_reports_it():
    fe = FakeFullEnrich({"hall": ("riley@acme.example", "DELIVERABLE")})
    p = EmailPipeline(StaticContacts([_contact("Riley", "Hall")]), finder=_finder(fe),
                      verifier=_verifier(FakeZeroBounce(error="ran out of credits")),
                      max_contacts_per_call=1)
    r = p.enrich(_account(), ["VP Sales"])
    c = r.contacts[0]
    assert (c.email_status, c.verified) == ("valid", True)
    assert c.email_checks == ["fullenrich:DELIVERABLE"]
    assert any("zerobounce" in f for f in r.source_failures)


@pytest.mark.unit
def test_pipeline_budget_never_lets_the_workflow_overspend(make_workflow):
    fe = FakeFullEnrich({"hall": ("x@acme.example", "DELIVERABLE")})
    wf = make_workflow()
    wf.enrichment = EmailPipeline(wf.enrichment, finder=_finder(fe), max_contacts_per_call=2)
    plan = wf.plan(limit=50)
    assert plan.enrichment_spent_cents <= wf.context.policy.enrichment_budget_cents

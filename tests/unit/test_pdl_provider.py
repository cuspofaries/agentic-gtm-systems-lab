"""PDLEnrichmentProvider against a fake transport reproducing measured free-plan behavior:
contact fields come back as booleans, credits spent are reported in a header, and an
empty search is a 404.
"""

from __future__ import annotations

import json

import pytest

from gtm_lab.context.models import BuyerPersona
from gtm_lab.models import Account, SourceAttribution
from gtm_lab.providers.pdl import SEARCH_URL, PDLEnrichmentProvider

KEY = "pdl-test-key"
PERSONAS = [
    BuyerPersona(name="VP Sales", titles_any_of=["vp sales", "chief revenue officer"]),
    BuyerPersona(name="Revenue Operations", titles_any_of=["revenue operations"]),
]


def _account():
    return Account(account_id="A007", domain="smartsheet.com", name="Smartsheet",
                   source=SourceAttribution(provider="test"))


class FakePDL:
    def __init__(self, status=200, records=None, spent=None, remaining=97):
        self.status, self.records, self.spent, self.remaining = status, records or [], spent, remaining
        self.calls = []

    def __call__(self, url, headers, body):
        assert url == SEARCH_URL and headers["X-Api-Key"] == KEY
        req = json.loads(body)
        self.calls.append(req)
        hdrs = {"x-totallimit-remaining": str(self.remaining)}
        if self.spent is not None:
            hdrs["x-call-credits-spent"] = str(self.spent)
        if self.status == 404:
            return 404, {"status": 404, "error": {"type": "not_found", "message": "No records"}}, hdrs
        if self.status >= 300:
            return self.status, {"error": {"message": "Payment required"}}, hdrs
        return 200, {"status": 200, "total": 653, "data": self.records[: req["size"]]}, hdrs


def _rec(first, last, title, email=True):
    return {"first_name": first, "last_name": last, "job_title": title, "work_email": email,
            "emails": True, "mobile_phone": True, "linkedin_url": f"linkedin.com/in/{first}-{last}"}


def _provider(fake, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return PDLEnrichmentProvider(KEY, PERSONAS, transport=fake, **kw)


@pytest.mark.unit
def test_query_targets_domain_and_persona_titles():
    fake = FakePDL(records=[_rec("riley", "hall", "vp sales, americas")], spent=1)
    _provider(fake, profiles_per_account=2).enrich(_account(), ["VP Sales", "Revenue Operations"])
    q = fake.calls[0]
    must = q["query"]["bool"]["must"]
    assert must[0] == {"term": {"job_company_website": "smartsheet.com"}}
    patterns = sorted(c["wildcard"]["job_title"] for c in must[1]["bool"]["should"])
    assert patterns == ["*chief*revenue*officer*", "*revenue*operations*", "*vp*sales*"]
    assert q["size"] == 2
    assert len(must) == 2  # no level filter unless asked


@pytest.mark.unit
def test_masked_free_plan_email_is_not_kept():
    fake = FakePDL(records=[_rec("riley", "hall", "vp sales", email=True)], spent=1)
    c = _provider(fake).enrich(_account(), ["VP Sales"]).contacts[0]
    assert c.email is None
    assert c.verified is False
    assert (c.first_name, c.last_name, c.title) == ("Riley", "Hall", "Vp Sales")
    assert c.source.url == "https://linkedin.com/in/riley-hall"


@pytest.mark.unit
def test_real_email_string_is_kept():
    fake = FakePDL(records=[_rec("sam", "ortiz", "revenue operations lead", email="sam@example.com")], spent=1)
    c = _provider(fake).enrich(_account(), ["VP Sales", "Revenue Operations"]).contacts[0]
    assert c.email == "sam@example.com"
    assert c.persona == "Revenue Operations"


@pytest.mark.unit
def test_cost_comes_from_credits_header():
    fake = FakePDL(records=[_rec("a", "b", "vp sales")], spent=1, remaining=96)
    p = _provider(fake, profiles_per_account=2, price_cents_per_profile=28)
    r = p.enrich(_account(), ["VP Sales"])
    assert p.max_cost_cents_per_call == 56
    assert r.cost_cents == 28
    assert p.credits_remaining == 96


@pytest.mark.unit
def test_no_match_is_empty_not_a_failure():
    r = _provider(FakePDL(status=404, spent=0)).enrich(_account(), ["VP Sales"])
    assert r.contacts == [] and r.source_failures == [] and r.cost_cents == 0


@pytest.mark.unit
def test_provider_error_is_a_source_failure():
    r = _provider(FakePDL(status=402, spent=0)).enrich(_account(), ["VP Sales"])
    assert r.contacts == []
    assert r.source_failures and "402" in r.source_failures[0]
    assert KEY not in r.source_failures[0]


@pytest.mark.unit
def test_personas_without_titles_are_rejected():
    p = PDLEnrichmentProvider(KEY, [BuyerPersona(name="X", titles_any_of=[])], transport=FakePDL())
    with pytest.raises(ValueError, match="titles_any_of"):
        p.build_query("a.com", ["X"])


@pytest.mark.unit
def test_budget_checks_the_maximum_and_charges_the_actual(make_workflow):
    from gtm_lab.providers.base import EnrichmentProvider, ProviderResult

    class Cheap(EnrichmentProvider):
        name = "cheap"
        cost_cents_per_call = 30  # announced maximum

        def enrich(self, account, personas):
            return ProviderResult(cost_cents=10)  # what it really cost

    wf = make_workflow()
    wf.enrichment = Cheap()
    plan = wf.plan(limit=50)
    # 50¢ budget, 30¢ announced maximum. Charged at the maximum: 1 call. Charged the real
    # 10¢: calls go on while 30¢ still fit (50 -> 40 -> 30 -> 20), so 3 calls for 30¢.
    assert plan.enrichment_spent_cents == 30
    assert sum(1 for e in plan.entries if e.enrichment_status == "done") == 3


@pytest.mark.unit
def test_title_levels_filter_is_added_when_asked():
    fake = FakePDL(records=[], spent=0)
    _provider(fake, title_levels=["VP", "director"]).enrich(_account(), ["VP Sales"])
    assert fake.calls[0]["query"]["bool"]["must"][2] == {"terms": {"job_title_levels": ["vp", "director"]}}


@pytest.mark.unit
def test_429_is_retried_after_the_window():
    class RateLimited(FakePDL):
        def __init__(self):
            super().__init__(records=[_rec("a", "b", "vp sales")], spent=1)
            self.n = 0

        def __call__(self, url, headers, body):
            self.n += 1
            if self.n == 1:
                return 429, {"error": {"message": "too quickly"}}, {"x-call-credits-spent": "0"}
            return super().__call__(url, headers, body)

    slept = []
    fake = RateLimited()
    r = _provider(fake, sleep=slept.append, retry_after_429_s=61).enrich(_account(), ["VP Sales"])
    assert len(r.contacts) == 1 and r.source_failures == []
    assert 61 in slept


@pytest.mark.unit
def test_calls_are_spaced_to_the_free_plan_rate():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(round(s, 2))
        now[0] += s

    p = _provider(FakePDL(records=[], spent=0), sleep=sleep, clock=lambda: now[0], min_interval_s=6)
    p.enrich(_account(), ["VP Sales"])
    now[0] += 1.5
    p.enrich(_account(), ["VP Sales"])
    assert slept == [4.5]


@pytest.mark.unit
def test_persona_is_the_most_specific_match():
    personas = [
        BuyerPersona(name="Sales leadership", titles_any_of=["vice president of sales", "head of sales"]),
        BuyerPersona(name="Revenue Operations", titles_any_of=["sales operations"]),
    ]
    p = PDLEnrichmentProvider(KEY, personas, transport=FakePDL(), sleep=lambda _s: None)
    names = ["Sales leadership", "Revenue Operations"]
    assert p._persona_for("vice president of sales operations", names) == "Revenue Operations"
    assert p._persona_for("regional vice president of sales", names) == "Sales leadership"
    assert p._persona_for("head of emea sales", names) == "Sales leadership"  # words in order


# --- company discovery ----------------------------------------------------


def _company(**over):
    base = {"id": "c1", "name": "cornerstone ondemand", "display_name": "Cornerstone OnDemand",
            "website": "cornerstoneondemand.com", "employee_count": 3258, "industry": "computer software",
            "tags": ["software as a service", "human resources"],
            "location": {"country": "united states", "region": "california"},
            "linkedin_url": "linkedin.com/company/cornerstoneondemand"}
    base.update(over)
    return base


class FakeCompanies:
    def __init__(self, records, status=200):
        self.records, self.status, self.calls = records, status, []

    def __call__(self, url, headers, body):
        from gtm_lab.providers.pdl import COMPANY_SEARCH_URL

        assert url == COMPANY_SEARCH_URL and headers["X-Api-Key"] == KEY
        self.calls.append(json.loads(body))
        if self.status >= 300:
            return self.status, {"error": {"message": "nope"}}, {}
        return 200, {"data": self.records}, {"x-call-credits-spent": str(len(self.records)),
                                             "x-totallimit-remaining": "90"}


def _discovery(fake, ctx):
    from gtm_lab.providers.pdl import PDLCompanyDiscoveryProvider

    return PDLCompanyDiscoveryProvider(KEY, ctx, transport=fake, sleep=lambda _s: None)


def _ctx(context, **disc):
    from gtm_lab.context.models import DiscoverySettings

    return context.model_copy(update={"discovery": DiscoverySettings(**disc)})


@pytest.mark.unit
def test_company_query_applies_icp_band_and_provider_filters(context):
    fake = FakeCompanies([])
    _discovery(fake, _ctx(context, pdl_industries=["Computer Software"], pdl_tags=["software as a service"])).discover(limit=15)
    must = fake.calls[0]["query"]["bool"]["must"]
    assert {"range": {"employee_count": {"gte": 20, "lte": 200}}} in must
    assert {"terms": {"location.country": ["united states"]}} in must
    assert {"terms": {"industry": ["computer software"]}} in must
    assert {"terms": {"tags": ["software as a service"]}} in must
    assert fake.calls[0]["size"] == 15


@pytest.mark.unit
def test_company_mapping_is_honest_about_its_source(context):
    r = _discovery(FakeCompanies([_company()]), _ctx(context)).discover(limit=5)
    a = r.accounts[0]
    assert (a.domain, a.name, a.employees, a.hq_country, a.hq_state) == (
        "cornerstoneondemand.com", "Cornerstone OnDemand", 3258, "US", "california")
    assert a.employees_confidence.value == "secondary"  # PDL estimate, not a filing
    assert "saas" in a.industry
    assert a.source.url == "https://linkedin.com/company/cornerstoneondemand"


@pytest.mark.unit
def test_company_without_website_is_dropped_and_missing_fields_flagged(context):
    recs = [_company(website=None), _company(id="c2", website="x.com", employee_count=None)]
    r = _discovery(FakeCompanies(recs), _ctx(context)).discover(limit=5)
    assert [a.domain for a in r.accounts] == ["x.com"]
    assert r.accounts[0].fields_missing == ["employees"]


@pytest.mark.unit
def test_company_search_error_is_reported_as_source_failure(context):
    r = _discovery(FakeCompanies([], status=402), _ctx(context)).discover(limit=5)
    assert r.accounts == [] and "402" in r.source_failures[0]

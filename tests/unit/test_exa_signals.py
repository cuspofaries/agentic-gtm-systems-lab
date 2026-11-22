"""Exa signal provider against a fake that reproduces the measured response shape:
`results[]` with `url`, `title`, `publishedDate`, `highlights`, and `costDollars.total`
(0.004 for one `lite` search)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gtm_lab.models import Account, SourceAttribution
from gtm_lab.providers.signals import EXA_SEARCH_URL, ExaSignalProvider, classify

KEY = "exa-test-key"
NOW = datetime(2026, 11, 22, tzinfo=UTC)


def _account(name="Acme Analytics, Inc.", domain="acme.example"):
    return Account(account_id="A1", domain=domain, name=name, source=SourceAttribution(provider="test"))


def _r(url, title, highlight="", days_ago=10):
    date = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z") if days_ago is not None else None
    return {"url": url, "title": title, "publishedDate": date, "highlights": [highlight]}


class FakeExa:
    def __init__(self, results=None, status=200, cost=0.004, error=None):
        self.results, self.status, self.cost, self.error = results or [], status, cost, error
        self.calls = []

    def __call__(self, method, url, headers, body):
        assert method == "POST" and url == EXA_SEARCH_URL and headers["x-api-key"] == KEY
        req = json.loads(body)
        assert KEY not in body.decode()
        self.calls.append(req)
        if self.status >= 300:
            return self.status, {"error": self.error or "Unauthorized"}
        out = {"results": self.results}
        if self.cost is not None:
            out["costDollars"] = {"total": self.cost, "search": {"neural": self.cost}}
        return 200, out


def _provider(fake, **kw):
    return ExaSignalProvider(KEY, transport=fake, sleep=lambda _s: None, now=lambda: NOW, **kw)


@pytest.mark.unit
def test_request_is_a_lite_search_limited_to_the_window():
    fake = FakeExa()
    _provider(fake, max_results=5).find(_account(), max_age_days=90)
    req = fake.calls[0]
    assert req["type"] == "lite" and req["numResults"] == 5
    assert req["startPublishedDate"] == "2026-08-24T00:00:00.000Z"
    assert req["contents"] == {"highlights": True}
    assert "category" not in req  # legacy parameter, Exa says not to use it
    assert "Acme Analytics" in req["query"] and "acme.example" in req["query"]


@pytest.mark.unit
def test_measured_cost_is_charged_in_thousandths_of_a_dollar():
    r = _provider(FakeExa(cost=0.004)).find(_account(), max_age_days=90)
    assert r.cost_millidollars == 4
    r = _provider(FakeExa(cost=None)).find(_account(), max_age_days=90)
    assert r.cost_millidollars == 4  # no figure: the announced maximum


@pytest.mark.unit
def test_checks_reject_what_is_not_a_dated_company_event():
    fake = FakeExa([
        _r("https://jobs.lever.co/acme/123", "Account Executive - Acme Analytics", "We are hiring an AE"),
        _r("https://linkedin.com/in/jane-doe", "Jane Doe", "VP of Sales at Acme Analytics"),
        _r("https://news.example/x", "Brad Smith joins Other Corp as VP of Sales", "Other Corp hires"),
        _r("https://news.example/y", "Acme Analytics raises Series B", "Acme raised $40M", days_ago=None),
        _r("https://news.example/z", "Acme Analytics opens London office", "", days_ago=200),
    ])
    r = _provider(fake).find(_account(), max_age_days=90)
    assert [s.kind for s in r.signals] == ["sales_hiring"]
    assert r.signals[0].confidence.value == "secondary"
    assert r.signals[0].source.url == "https://jobs.lever.co/acme/123"
    assert dict(r.rejected) == {"personal_profile": 1, "not_about_company": 1, "undated": 1, "outside_window": 1}


@pytest.mark.unit
def test_company_matched_by_its_domain_or_on_its_own_site():
    fake = FakeExa([
        _r("https://acme.example/newsroom/cro", "Welcome to our new Chief Revenue Officer", "We appointed a CRO"),
        _r("https://press.example/a", "Partnership", "acme.example announces funding round"),
    ])
    r = _provider(fake).find(_account(), max_age_days=90)
    assert [s.kind for s in r.signals] == ["new_sales_leadership", "revenue_expansion"]


@pytest.mark.unit
def test_a_name_found_only_in_the_body_is_a_passing_mention():
    # Measured: a vendor's use-case page with a table of several companies.
    fake = FakeExa([_r("https://vendor.example/use-cases/x", "Find companies outgrowing their setup",
                       "| Acme Analytics | 370 | Support lead departed; two backfills posted |")])
    r = _provider(fake).find(_account(), max_age_days=90)
    assert r.signals == [] and dict(r.rejected) == {"mentioned_in_passing": 1}


@pytest.mark.unit
def test_first_word_of_the_domain_is_not_enough():
    # Measured: "BrightPath Analytics" (brightpath.example) matched a post by another
    # company called BrightPath when the domain's first label counted as the company.
    acc = _account(name="BrightPath Analytics", domain="brightpath.example")
    fake = FakeExa([_r("https://linkedin.com/posts/brightpathassociates_hiring", "BrightPath Associates is hiring",
                       "We're hiring a Sales Manager at BrightPath Associates")])
    r = _provider(fake).find(acc, max_age_days=90)
    assert r.signals == [] and r.rejected["not_about_company"] == 1


@pytest.mark.unit
def test_short_names_do_not_match_inside_other_words():
    acc = _account(name="Box", domain="box.example")
    r = _provider(FakeExa([_r("https://news.example/a", "Dropbox raises funding", "")])).find(acc, max_age_days=90)
    assert r.signals == [] and r.rejected["not_about_company"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("text,url,kind", [
    ("Acme names Jane Doe Chief Revenue Officer", "https://n.example/a", "new_sales_leadership"),
    ("Acme hires a new VP of Sales", "https://n.example/a", "new_sales_leadership"),
    ("Sales Development Representative - apply now", "https://n.example/a", "sales_hiring"),
    ("Enterprise Sales Manager", "https://boards.greenhouse.io/acme/jobs/1", "sales_hiring"),
    ("Acme acquires Beta", "https://n.example/a", "revenue_expansion"),
    ("Acme publishes its annual report", "https://n.example/a", "other"),
    # Measured on real results: these were misread before.
    ("I'm excited to share that I've joined Acme as VP of Revenue Operations", "https://www.linkedin.com/posts/x", "new_sales_leadership"),
    ("Acme is hiring: Senior Account Manager", "https://www.linkedin.com/posts/y", "sales_hiring"),
    ("Account Manager @ Acme", "https://jobs.ashbyhq.com/acme/625c", "sales_hiring"),
    ("News: Acme expands to Europe", "https://www.acme.example/news", "other"),
    ("Acme raised its Series A", "https://www.linkedin.com/company/acme", "other"),
])
def test_classification(text, url, kind):
    assert classify(text, url) == kind


@pytest.mark.unit
def test_http_error_is_a_failure_without_the_key():
    r = _provider(FakeExa(status=401, error="Invalid API key")).find(_account(), max_age_days=90)
    assert r.signals == [] and r.cost_millidollars == 0
    assert r.failures == ["exa:acme.example:HTTP 401: Invalid API key"]
    assert KEY not in r.failures[0]


@pytest.mark.unit
def test_constructor_refuses_an_empty_key_and_paid_extra_results():
    with pytest.raises(ValueError):
        ExaSignalProvider("")
    with pytest.raises(ValueError):
        ExaSignalProvider(KEY, max_results=11)


class _StaticSignals(ExaSignalProvider):
    """Every account gets one fresh sales-hiring page."""

    def __init__(self):
        super().__init__(KEY, transport=self._answer, sleep=lambda _s: None)
        self.calls = 0

    def _answer(self, method, url, headers, body):
        self.calls += 1
        name = json.loads(body)["query"].split(" (")[0]
        today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00.000Z")
        return 200, {"results": [{"url": "https://jobs.lever.co/x/1", "title": f"Account Executive - {name}",
                                  "publishedDate": today, "highlights": ["We are hiring"]}],
                     "costDollars": {"total": 0.004}}


@pytest.mark.unit
def test_workflow_attaches_signals_and_stops_at_the_signal_budget(make_workflow, context):
    ctx = context.model_copy(deep=True)
    ctx.signals.budget_millidollars = 12  # three searches
    wf = make_workflow(ctx)
    wf.signals = _StaticSignals()
    plan = wf.plan(limit=50)
    assert wf.signals.calls == 3
    assert plan.signals_spent_millidollars == 12 and plan.signals_budget_millidollars == 12
    with_signal = [a for a in wf.store.load_accounts(plan.run_id) if any(s.source.provider == "exa" for s in a.signals)]
    assert len(with_signal) == 3


@pytest.mark.unit
def test_signal_makes_strict_policy_qualify(make_workflow, context):
    ctx = context.model_copy(deep=True)
    ctx.policy.require_signal_for_qualification = True
    without = make_workflow(ctx).plan(limit=50, run_id="no_signals")
    wf = make_workflow(ctx)
    wf.signals = _StaticSignals()
    with_signals = wf.plan(limit=50, run_id="with_signals")
    q = lambda p: sum(1 for d in wf.store.load_decisions(p.run_id) if d.decision == "qualified")  # noqa: E731
    assert q(with_signals) > q(without)

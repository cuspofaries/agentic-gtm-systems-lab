"""CI self-test of the opt-in real-data case (tests/realcase), with fake providers.

Records a full run against fakes of People Data Labs, FullEnrich, ZeroBounce and HubSpot,
then replays it with the network cut, and checks that expected results are enforced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from gtm_lab.crm import hubspot as hubspot_mod
from gtm_lab.providers import email as email_mod
from gtm_lab.providers import pdl as pdl_mod
from gtm_lab.providers.pdl import COMPANY_SEARCH_URL, SEARCH_URL
from tests.realcase.harness import run_case
from tests.unit.test_email_pipeline import FE_KEY, ZB_KEY, FakeFullEnrich, FakeZeroBounce
from tests.unit.test_hubspot_adapter import TOKEN, FakeHubSpot

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "account-discovery-to-crm"
PDL_KEY = "pdl-test-key"
KEYS = {"PDL_API_KEY": PDL_KEY, "FULLENRICH_API_KEY": FE_KEY,
        "ZEROBOUNCE_API_KEY": ZB_KEY, "HUBSPOT_SERVICE_KEY": TOKEN}

COMPANIES = [
    {"id": "c1", "website": "alpha.example", "name": "alpha", "employee_count": 80,
     "industry": "computer software", "tags": ["software as a service"], "location": {"country": "united states"}},
    {"id": "c2", "website": "beta.example", "name": "beta", "employee_count": 120,
     "industry": "computer software", "tags": ["software as a service"], "location": {"country": "united states"}},
    {"id": "c3", "website": "gamma.example", "name": "gamma", "employee_count": 60,
     "industry": "computer software", "tags": ["software as a service"], "location": {"country": "united states"}},
]
PEOPLE = {"alpha.example": ("Riley", "Hall"), "beta.example": ("Sam", "Ortiz"), "gamma.example": ("Ana", "Diaz")}


def _fake_pdl(url, headers, body):
    assert headers["X-Api-Key"] == PDL_KEY
    q = json.loads(body)
    if url == COMPANY_SEARCH_URL:
        return 200, {"status": 200, "total": 3, "data": COMPANIES}, {"x-call-credits-spent": "3"}
    assert url == SEARCH_URL
    domain = next(d for d in PEOPLE if d in json.dumps(q))
    first, last = PEOPLE[domain]
    rec = {"first_name": first.lower(), "last_name": last.lower(), "job_title": "vp sales",
           "work_email": True, "linkedin_url": f"linkedin.com/in/{first}-{last}"}
    return 200, {"status": 200, "total": 1, "data": [rec]}, {"x-call-credits-spent": "1"}


@pytest.fixture
def case(tmp_path):
    d = tmp_path / "case"
    (d / "config").mkdir(parents=True)
    ctx = yaml.safe_load((EXAMPLE / "config" / "gtm-context.yaml").read_text())
    ctx["policy"]["enrichment_budget_cents"] = 500
    (d / "config" / "gtm-context.yaml").write_text(yaml.safe_dump(ctx))
    run = {"limit": 3, "profiles_per_account": 1, "title_levels": ["vp"],
           "email_finder_cents": 10, "email_verifier_cents": 2}
    (d / "expected.yaml").write_text(yaml.safe_dump({"run": run}))
    return d


@pytest.fixture
def fake_network(monkeypatch):
    hub = FakeHubSpot()
    hub.companies["900"] = {"id": "900", "properties": {"domain": "beta.example", "name": "Beta"}}
    fe = FakeFullEnrich({"hall": ("riley@alpha.example", "DELIVERABLE"),
                         "diaz": ("ana@gamma.example", "CATCH_ALL")})
    zb = FakeZeroBounce({"ana@gamma.example": ("catch-all", "")})

    def email(method, url, headers, body):
        return (zb if "zerobounce" in url else fe)(method, url, headers, body)

    monkeypatch.setattr(pdl_mod, "urllib_transport", _fake_pdl)
    monkeypatch.setattr(email_mod, "urllib_transport", email)
    monkeypatch.setattr(hubspot_mod, "urllib_transport", hub)
    return hub


def _cut_network(monkeypatch):
    def boom(*_a):
        raise AssertionError("replay reached the network")
    for mod in (pdl_mod, email_mod, hubspot_mod):
        monkeypatch.setattr(mod, "urllib_transport", boom)


@pytest.mark.integration
def test_record_then_replay_offline_against_expected(case, fake_network, monkeypatch, tmp_path):
    observed, diffs = run_case(case, "record", KEYS, tmp_path / "s1", no_wait=True)
    assert diffs is None  # no expectations yet: a person reviews observed.yaml first
    assert observed["credits"] == {"pdl_company": 3, "pdl_person": 2, "fullenrich": 2, "zerobounce": 2}
    assert observed["skip_existing"] == ["beta.example"]
    assert observed["create_account"] == ["alpha.example", "gamma.example"]
    assert observed["emails_by_status"]["valid"] == 1 and observed["emails_by_status"]["risky"] == 1
    for key in KEYS.values():
        assert key not in (case / "cassette.json").read_text()

    spec = yaml.safe_load((case / "expected.yaml").read_text())
    spec["expect"] = yaml.safe_load((case / "observed.yaml").read_text())
    (case / "expected.yaml").write_text(yaml.safe_dump(spec))

    _cut_network(monkeypatch)
    replayed, diffs = run_case(case, "replay", dict.fromkeys(KEYS, "replay"), tmp_path / "s2")
    assert diffs == {} and replayed == observed


@pytest.mark.integration
def test_replay_reports_what_differs_from_expected(case, fake_network, monkeypatch, tmp_path):
    run_case(case, "record", KEYS, tmp_path / "s1", no_wait=True)
    spec = yaml.safe_load((case / "expected.yaml").read_text())
    spec["expect"] = {"skip_existing": ["alpha.example"], "contacts_proposed": 2}
    (case / "expected.yaml").write_text(yaml.safe_dump(spec))
    _cut_network(monkeypatch)
    _obs, diffs = run_case(case, "replay", dict.fromkeys(KEYS, "replay"), tmp_path / "s2")
    assert diffs == {"skip_existing": {"expected": ["alpha.example"], "observed": ["beta.example"]}}


@pytest.mark.integration
def test_a_crm_write_during_planning_fails_the_case(case, fake_network, monkeypatch, tmp_path):
    run_case(case, "record", KEYS, tmp_path / "s1", no_wait=True)
    cassette = json.loads((case / "cassette.json").read_text())
    cassette["exchanges"].append({"provider": "hubspot", "method": "POST", "url": "https://x/crm/objects/2026-09/companies",
                                  "body": None, "status": 201, "response": {}, "headers": {}})
    (case / "cassette.json").write_text(json.dumps(cassette))
    _cut_network(monkeypatch)
    with pytest.raises(AssertionError, match="must not change the CRM"):
        run_case(case, "replay", dict.fromkeys(KEYS, "replay"), tmp_path / "s2")


def _fake_exa(method, url, headers, body):
    assert headers["x-api-key"] == "exa-test-key"
    name = json.loads(body)["query"].split(" (")[0]
    from datetime import UTC, datetime
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00.000Z")
    return 200, {"results": [
        {"url": "https://jobs.lever.co/x/1", "title": f"Account Executive - {name}", "publishedDate": today,
         "highlights": ["We are hiring"]},
        {"url": "https://linkedin.com/in/someone", "title": "Someone", "publishedDate": today, "highlights": []},
    ], "costDollars": {"total": 0.004}}


@pytest.mark.integration
def test_signals_are_recorded_and_replayed_as_of_the_recording_day(case, fake_network, monkeypatch, tmp_path):
    from gtm_lab.providers import signals as signals_mod

    monkeypatch.setattr(signals_mod, "urllib_transport", _fake_exa)
    spec = yaml.safe_load((case / "expected.yaml").read_text())
    spec["run"]["signals"] = "exa"
    (case / "expected.yaml").write_text(yaml.safe_dump(spec))
    keys = KEYS | {"EXA_API_KEY": "exa-test-key"}

    observed, _ = run_case(case, "record", keys, tmp_path / "s1", no_wait=True)
    assert observed["signals_by_kind"] == {"sales_hiring": 3}
    assert observed["signals_spent_millidollars"] == 12
    assert observed["credits"]["exa_millidollars"] == 12
    assert "exa-test-key" not in (case / "cassette.json").read_text()

    spec["expect"] = observed
    (case / "expected.yaml").write_text(yaml.safe_dump(spec))
    _cut_network(monkeypatch)
    monkeypatch.setattr(signals_mod, "urllib_transport", lambda *_a: (_ for _ in ()).throw(AssertionError("network")))
    _obs, diffs = run_case(case, "replay", dict.fromkeys(keys, "replay"), tmp_path / "s2")
    assert diffs == {}

    # The search window comes from the recording date, not from today: move that date
    # and the replayed requests no longer match what was recorded.
    cassette = json.loads((case / "cassette.json").read_text())
    cassette["recorded_at"] = "2025-01-01T00:00:00+00:00"
    (case / "cassette.json").write_text(json.dumps(cassette))
    with pytest.raises(AssertionError, match="not recorded"):
        run_case(case, "replay", dict.fromkeys(keys, "replay"), tmp_path / "s3")


@pytest.mark.integration
def test_research_loop_is_recorded_and_replayed(case, fake_network, monkeypatch, tmp_path):
    """Claude's turns and the Exa calls of the research loop replay offline, and the
    replayed loop rebuilds exactly the requests that were recorded."""
    import anthropic

    from gtm_lab.providers import signals as signals_mod
    from tests.unit.test_research_loop import FakeExa, ScriptedClaude, _resp, _use

    page = "https://news.example/headcount"
    exa = FakeExa(results=[{"url": page, "title": "Headcount", "publishedDate": "2026-11-01T00:00:00.000Z",
                            "highlights": ["about 90 employees"]}],
                  page_text="The company has about 90 employees in the US.")

    def exa_with_key(method, url, headers, body):
        return exa(method, url, {**headers, "x-api-key": "exa-key"}, body)

    def turns():
        return [_resp(_use("search", 1, query="headcount")), _resp(_use("read", 2, url=page)),
                _resp(_use("submit", 3, decision="qualified", explanation="90 employees",
                           evidence=[{"url": page, "quote": "about 90 employees in the US"}]))]

    scripted = ScriptedClaude(turns() * 3)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: scripted)
    monkeypatch.setattr(signals_mod, "urllib_transport", exa_with_key)
    spec = yaml.safe_load((case / "expected.yaml").read_text())
    spec["run"] |= {"research": "exa", "drop_fields": ["employees"], "enrichment": "none"}
    (case / "expected.yaml").write_text(yaml.safe_dump(spec))
    keys = KEYS | {"EXA_API_KEY": "exa-test-key", "ANTHROPIC_API_KEY": "sk-test-anthropic"}

    observed, _ = run_case(case, "record", keys, tmp_path / "s1", no_wait=True)
    assert set(observed["research_decisions"].values()) == {"qualified"}
    assert set(observed["research_evidence_kept"].values()) == {1}
    assert observed["contacts_proposed"] == 0 and observed["research_llm_calls"] == 9
    text = (case / "cassette.json").read_text()
    assert "sk-test-anthropic" not in text and "exa-test-key" not in text

    spec["expect"] = observed
    (case / "expected.yaml").write_text(yaml.safe_dump(spec))
    _cut_network(monkeypatch)
    monkeypatch.setattr(signals_mod, "urllib_transport", lambda *_a: (_ for _ in ()).throw(AssertionError("net")))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: (_ for _ in ()).throw(AssertionError("net")))
    _obs, diffs = run_case(case, "replay", dict.fromkeys(keys, "replay"), tmp_path / "s2")
    assert diffs == {}

"""The record/replay cassette behind the opt-in real-data test (tests/realcase)."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from tests.realcase.cassette import Cassette, CassetteMissError

KEY = "secret-key-123"


def _pdl_real(url, headers, body):
    assert headers["X-Api-Key"] == KEY
    return 200, {"data": [{"name": "acme"}]}, {"X-Call-Credits-Spent": "1", "Set-Cookie": "s=1"}


def _zb_real(method, url, headers, body):
    form = dict(urllib.parse.parse_qsl(body.decode()))
    status = "unknown" if form["email"].startswith("u") else "valid"
    return 200, {"address": form["email"], "status": status}


def _zb_body(email, key=KEY):
    return urllib.parse.urlencode({"api_key": key, "email": email}).encode()


@pytest.mark.unit
def test_record_then_replay_gives_the_same_answers_without_the_network(tmp_path):
    path = tmp_path / "c.json"
    rec = Cassette(path, "record", secrets=(KEY,))
    t = rec.pdl(_pdl_real)
    first = t("https://pdl/company/search", {"X-Api-Key": KEY}, b'{"size": 2}')
    rec.save()

    def no_network(*_a):
        raise AssertionError("replay reached the network")

    rep = Cassette(path, "replay")
    again = rep.pdl(no_network)("https://pdl/company/search", {"X-Api-Key": "other"}, b'{"size":2}')
    assert again[:2] == first[:2]
    assert again[2] == {"x-call-credits-spent": "1"}  # only the headers connectors read
    assert rep.unused() == []


@pytest.mark.unit
def test_keys_never_reach_the_file(tmp_path):
    path = tmp_path / "c.json"
    rec = Cassette(path, "record", secrets=(KEY,))
    rec.email("zerobounce", _zb_real)("POST", "https://zb/v2/validate", {}, _zb_body("a@x.example"))
    rec.save()
    text = path.read_text()
    assert KEY not in text and "api_key" not in text
    # Replay matches on the body without the key, whatever key the replay uses.
    rep = Cassette(path, "replay")
    _status, payload = rep.email("zerobounce", None)("POST", "https://zb/v2/validate", {}, _zb_body("a@x.example", "x"))
    assert payload["status"] == "valid"


@pytest.mark.unit
def test_save_refuses_a_cassette_that_contains_a_key(tmp_path):
    def leaky(url, headers, body):
        return 200, {"echo": KEY}, {}

    rec = Cassette(tmp_path / "c.json", "record", secrets=(KEY,))
    rec.pdl(leaky)("https://pdl/person/search", {}, b"{}")
    with pytest.raises(AssertionError, match="API key"):
        rec.save()
    assert not (tmp_path / "c.json").exists()


@pytest.mark.unit
def test_replay_fails_on_a_request_it_has_not_seen(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"exchanges": []}))
    with pytest.raises(CassetteMissError):
        Cassette(path, "replay").pdl(None)("https://pdl/company/search", {}, b"{}")


@pytest.mark.unit
def test_repeated_polls_replay_in_recorded_order(tmp_path):
    answers = iter([(400, {"code": "error.enrichment.in_progress"}), (200, {"status": "FINISHED"})])
    rec = Cassette(tmp_path / "c.json", "record")
    poll = rec.email("fullenrich", lambda *_a: next(answers))
    recorded = [poll("GET", "https://fe/bulk/1", {}, None) for _ in range(2)]
    rec.save()
    rep = Cassette(tmp_path / "c.json", "replay").email("fullenrich", None)
    assert [rep("GET", "https://fe/bulk/1", {}, None) for _ in range(2)] == recorded


@pytest.mark.unit
def test_credits_and_crm_writes_are_read_from_the_exchanges(tmp_path):
    rec = Cassette(tmp_path / "c.json", "record")
    rec.pdl(_pdl_real)("https://pdl/v5/company/search", {"X-Api-Key": KEY}, b"{}")
    rec.pdl(_pdl_real)("https://pdl/v5/person/search", {"X-Api-Key": KEY}, b"{}")
    rec.email("fullenrich", lambda *_a: (200, {"status": "FINISHED", "cost": {"credits": 2}}))(
        "GET", "https://fe/bulk/1", {}, None)
    zb = rec.email("zerobounce", _zb_real)
    zb("POST", "https://zb/v2/validate", {}, _zb_body("a@x.example"))
    zb("POST", "https://zb/v2/validate", {}, _zb_body("u@x.example"))  # unknown is free
    hs = rec.hubspot(lambda *_a: (200, {}, {}))
    hs("POST", "https://hs/crm/objects/2026-09/companies/search", {}, b"{}")
    hs("GET", "https://hs/account-info/v3/details", {}, None)
    assert rec.writes() == []
    hs("POST", "https://hs/crm/objects/2026-09/companies", {}, b"{}")
    assert rec.writes() == ["POST https://hs/crm/objects/2026-09/companies"]
    assert rec.credits() == {"pdl_company": 1, "pdl_person": 1, "fullenrich": 2, "zerobounce": 1}

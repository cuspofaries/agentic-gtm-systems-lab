from datetime import UTC, datetime, timedelta

import pytest

from gtm_lab.util import canonicalize_domain, days_ago, is_stale, within_window


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw, want",
    [
        ("http://www.Example.COM/foo", "example.com"),
        ("HTTPS://api.example.com/", "api.example.com"),
        ("  ACME.IO  ", "acme.io"),
        ("www.acme.io", "acme.io"),
        ("example.com", "example.com"),
    ],
)
def test_canonicalize_domain(raw, want):
    assert canonicalize_domain(raw) == want


@pytest.mark.unit
def test_days_ago_and_window():
    now = datetime(2026, 11, 21, tzinfo=UTC)
    d1 = now - timedelta(days=10)
    assert days_ago(d1, now=now) == 10
    assert within_window(d1, 30, now=now)
    assert not within_window(d1, 5, now=now)
    assert days_ago(None) is None


@pytest.mark.unit
def test_is_stale():
    now = datetime(2026, 11, 21, tzinfo=UTC)
    assert is_stale(None, 30, now=now)
    assert is_stale(now - timedelta(days=100), 90, now=now)
    assert not is_stale(now - timedelta(days=30), 90, now=now)

"""Shared small utilities. Kept deliberately thin - if it grows, split it."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_WWW_RE = re.compile(r"^www\.", re.IGNORECASE)
_TRAIL_SLASH_RE = re.compile(r"/.*$")


def canonicalize_domain(raw: str) -> str:
    """Normalize a company domain for deterministic matching.

    `  HTTP://WWW.Example.COM/foo?x=1 ` → `example.com`
    """
    d = raw.strip().lower()
    d = _SCHEME_RE.sub("", d)
    d = _WWW_RE.sub("", d)
    d = _TRAIL_SLASH_RE.sub("", d)
    return d


def days_ago(dt: datetime | None, *, now: datetime | None = None) -> int | None:
    """Days between `dt` and `now`. Returns None if dt is None."""
    if dt is None:
        return None
    now = now or datetime.now(UTC)
    return (now - dt).days


def within_window(dt: datetime | None, days: int, *, now: datetime | None = None) -> bool:
    """True iff dt is within the last `days` days."""
    d = days_ago(dt, now=now)
    return d is not None and 0 <= d <= days


def is_stale(dt: datetime | None, max_age_days: int, *, now: datetime | None = None) -> bool:
    """True iff dt is older than max_age_days (missing dt is treated as stale)."""
    d = days_ago(dt, now=now)
    return d is None or d > max_age_days


def utcnow() -> datetime:
    return datetime.now(UTC)


def ts_slug(dt: datetime | None = None) -> str:
    """UTC timestamp slug suitable for run ids: `20261121T215959Z`."""
    return (dt or utcnow()).strftime("%Y%m%dT%H%M%SZ")


def human_dt(dt: datetime) -> str:
    """Short human-readable datetime for review artifacts."""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


__all__ = [
    "canonicalize_domain",
    "days_ago",
    "human_dt",
    "is_stale",
    "timedelta",
    "ts_slug",
    "utcnow",
    "within_window",
]

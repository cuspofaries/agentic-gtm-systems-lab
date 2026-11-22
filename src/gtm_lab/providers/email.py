"""Find and verify contact emails, as one optional step after contact enrichment.

`EmailPipeline` wraps any EnrichmentProvider. For each account it:

1. gets contacts from the wrapped provider (e.g. People Data Labs, which gives names and
   LinkedIn URLs but no email on its free plan);
2. asks an EmailFinder for the missing emails (FullEnrich);
3. asks an EmailVerifier to check every email (ZeroBounce);
4. applies the context rule for emails that are not confirmed valid.

It is an EnrichmentProvider itself, so the workflow and its budget do not change: the
pipeline announces the most one account can cost (contacts, then one find and one check
per contact) and reports what was actually spent.

Every verdict is normalized to valid / risky / invalid / unknown. Only `valid` sets
`ContactProposal.verified`. An `invalid` email is always removed; what happens to `risky`
and `unknown` is the context's `contacts.on_unverified_email`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from ..models import Account, ContactProposal
from .base import EnrichmentProvider, ProviderResult

EmailStatus = Literal["valid", "risky", "invalid", "unknown"]
# (method, url, headers, body) -> (http status, parsed JSON or None)
Transport = Callable[[str, str, dict, bytes | None], tuple[int, object]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


# --- interfaces ---------------------------------------------------------------------


@dataclass
class FoundEmail:
    email: str
    status: EmailStatus
    detail: str  # the provider's own label, e.g. "fullenrich:CATCH_ALL"


@dataclass
class FindResult:
    found: dict[int, FoundEmail] = field(default_factory=dict)  # contact index -> email
    cost_cents: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    status: EmailStatus
    detail: str  # e.g. "zerobounce:catch-all"
    cost_cents: int = 0
    failure: str | None = None


class EmailFinder(ABC):
    name: str = "email-finder"
    cost_cents_per_email: int = 0  # most one found email can cost

    @abstractmethod
    def find(self, account: Account, contacts: list[ContactProposal]) -> FindResult: ...


class EmailVerifier(ABC):
    name: str = "email-verifier"
    cost_cents_per_check: int = 0  # most one check can cost

    @abstractmethod
    def verify(self, email: str) -> Verdict: ...


# --- FullEnrich ---------------------------------------------------------------------

FULLENRICH_API = "https://app.fullenrich.com/api/v2"
FULLENRICH_STATUS: dict[str, EmailStatus] = {
    "DELIVERABLE": "valid",
    "HIGH_PROBABILITY": "risky",
    "CATCH_ALL": "risky",
    "INVALID": "invalid",
    "INVALID_DOMAIN": "invalid",
    "UNVERIFIED": "unknown",
}


class FullEnrichFinder(EmailFinder):
    """Work emails from FullEnrich's bulk enrichment API (v2).

    Enrichment is asynchronous: one POST per account starts it, then the result is polled
    until FINISHED. Only `contact.work_emails` is requested (no phones). FullEnrich charges
    one credit per email found and nothing when none is found.
    """

    name = "fullenrich"

    def __init__(
        self,
        api_key: str,
        *,
        cents_per_credit: int,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        first_poll_s: float = 10,
        poll_every_s: float = 15,
        timeout_s: float = 300,
    ) -> None:
        self._key = api_key
        self.cost_cents_per_email = cents_per_credit
        self._transport, self._sleep = transport, sleep
        self._first_poll_s, self._poll_every_s, self._timeout_s = first_poll_s, poll_every_s, timeout_s

    def _call(self, method: str, path: str, body: dict | None = None):
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        return self._transport(method, FULLENRICH_API + path, headers, data)

    def find(self, account: Account, contacts: list[ContactProposal]) -> FindResult:
        result = FindResult()
        items = []
        for i, c in enumerate(contacts):
            if c.email or not (c.first_name and c.last_name):
                continue
            item = {
                "first_name": c.first_name,
                "last_name": c.last_name,
                "domain": account.domain,
                "company_name": account.name,
                "enrich_fields": ["contact.work_emails"],
                "custom": {"idx": str(i)},
            }
            url = c.source.url or ""
            if "linkedin.com/in/" in url:
                item["linkedin_url"] = url
            items.append(item)
        if not items:
            return result
        try:
            status, payload = self._call(
                "POST", "/contact/enrich/bulk",
                {"name": f"gtm-lab {account.domain}", "data": items},
            )
            if status != 200 or not isinstance(payload, dict) or "enrichment_id" not in payload:
                result.failures.append(f"fullenrich: start failed (HTTP {status}{_msg(payload)})")
                return result
            payload = self._wait(payload["enrichment_id"], result)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            result.failures.append(f"fullenrich: network error ({type(e).__name__})")
            return result
        if payload is None:
            return result
        credits = (payload.get("cost") or {}).get("credits")
        for row in payload.get("data") or []:
            idx = (row.get("custom") or {}).get("idx")
            best = (row.get("contact_info") or {}).get("most_probable_work_email") or {}
            email, label = best.get("email"), best.get("status")
            if idx is None or not email:
                continue
            result.found[int(idx)] = FoundEmail(
                email=email.strip().lower(),
                status=FULLENRICH_STATUS.get(str(label), "unknown"),
                detail=f"fullenrich:{label}",
            )
        n = len(result.found) if credits is None else int(credits)
        result.cost_cents = n * self.cost_cents_per_email
        return result

    def _wait(self, enrichment_id: str, result: FindResult) -> dict | None:
        waited = self._first_poll_s
        self._sleep(self._first_poll_s)
        while True:
            status, payload = self._call("GET", f"/contact/enrich/bulk/{enrichment_id}")
            state = payload.get("status") if isinstance(payload, dict) else None
            if status == 200 and state == "FINISHED":
                return payload
            not_ready = (status == 400 and _code(payload) == "error.enrichment.in_progress") or (
                status == 200 and state in ("CREATED", "IN_PROGRESS")
            ) or status == 429
            if not not_ready:
                result.failures.append(
                    f"fullenrich: enrichment {state or 'failed'} (HTTP {status}{_msg(payload)})"
                )
                # CREDITS_INSUFFICIENT came back as HTTP 402 on a real run: keep what was found.
                if status in (200, 402) and isinstance(payload, dict) and "data" in payload:
                    return payload
                return None
            if waited >= self._timeout_s:
                result.failures.append(f"fullenrich: not finished after {int(waited)}s")
                return None
            pause = 60 if status == 429 else self._poll_every_s
            self._sleep(pause)
            waited += pause


# --- ZeroBounce ---------------------------------------------------------------------

ZEROBOUNCE_API = "https://api.zerobounce.net/v2"
ZEROBOUNCE_STATUS: dict[str, EmailStatus] = {
    "valid": "valid",
    "catch-all": "risky",
    "unknown": "unknown",
    "invalid": "invalid",
    "spamtrap": "invalid",
    "abuse": "invalid",
    "do_not_mail": "invalid",
}


class ZeroBounceVerifier(EmailVerifier):
    """Single-email validation (ZeroBounce v2 /validate).

    The key goes in a POST form body, never in a URL. ZeroBounce charges one credit per
    check and none for `unknown`. A bad key or an empty balance comes back as HTTP 200
    with an `error` field (measured), and is reported as a failure, not as a verdict.
    """

    name = "zerobounce"

    def __init__(
        self,
        api_key: str,
        *,
        cents_per_credit: int,
        base_url: str = ZEROBOUNCE_API,
        timeout_s: int = 30,
        transport: Transport = urllib_transport,
    ) -> None:
        self._key = api_key
        self.cost_cents_per_check = cents_per_credit
        self._base, self._timeout_s, self._transport = base_url, timeout_s, transport

    def verify(self, email: str) -> Verdict:
        body = urllib.parse.urlencode(
            {"api_key": self._key, "email": email, "ip_address": "", "timeout": self._timeout_s}
        ).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        try:
            status, payload = self._transport("POST", self._base + "/validate", headers, body)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return Verdict("unknown", "zerobounce:error", failure=f"zerobounce: network error ({type(e).__name__})")
        if status != 200 or not isinstance(payload, dict) or "error" in payload or "status" not in payload:
            return Verdict("unknown", "zerobounce:error",
                           failure=f"zerobounce: HTTP {status}{_msg(payload)}")
        raw = str(payload["status"])
        sub = payload.get("sub_status") or ""
        verdict = ZEROBOUNCE_STATUS.get(raw, "unknown")
        charged = 0 if raw == "unknown" else self.cost_cents_per_check
        return Verdict(verdict, f"zerobounce:{raw}" + (f"/{sub}" if sub else ""), cost_cents=charged)


# --- the pipeline -------------------------------------------------------------------


class EmailPipeline(EnrichmentProvider):
    """Contacts from `base`, then emails found by `finder` and checked by `verifier`."""

    def __init__(
        self,
        base: EnrichmentProvider,
        *,
        finder: EmailFinder | None = None,
        verifier: EmailVerifier | None = None,
        max_contacts_per_call: int,
        on_unverified: Literal["keep", "drop_email"] = "keep",
    ) -> None:
        self.base, self.finder, self.verifier = base, finder, verifier
        self.max_contacts = max_contacts_per_call
        self.on_unverified = on_unverified
        self.name = "+".join(p.name for p in (base, finder, verifier) if p is not None)

    @property
    def max_cost_cents_per_call(self) -> int:
        per_contact = (self.finder.cost_cents_per_email if self.finder else 0) + (
            self.verifier.cost_cents_per_check if self.verifier else 0
        )
        return self.base.max_cost_cents_per_call + self.max_contacts * per_contact

    def enrich(self, account: Account, personas: Iterable[str]) -> ProviderResult:
        res = self.base.enrich(account, personas)
        base_cost = self.base.max_cost_cents_per_call if res.cost_cents is None else res.cost_cents
        contacts = list(res.contacts)[: self.max_contacts]
        failures = list(res.source_failures)
        cost = base_cost
        checks: dict[int, list[str]] = {i: [] for i in range(len(contacts))}
        status: dict[int, EmailStatus] = {}

        if self.finder and contacts:
            found = self.finder.find(account, contacts)
            cost += found.cost_cents
            failures += found.failures
            for i, f in found.found.items():
                contacts[i] = contacts[i].model_copy(update={"email": f.email})
                checks[i].append(f.detail)
                status[i] = f.status

        if self.verifier:
            for i, c in enumerate(contacts):
                if not c.email:
                    continue
                v = self.verifier.verify(c.email)
                cost += v.cost_cents
                if v.failure:
                    failures.append(v.failure)
                    continue  # no verdict: keep the finder's, if any
                checks[i].append(v.detail)
                status[i] = v.status  # the dedicated check has the last word

        out = []
        for i, c in enumerate(contacts):
            default = ("valid" if c.verified else "unknown") if c.email else None
            st = status.get(i, default)
            email = c.email
            if email and (st == "invalid" or (st != "valid" and self.on_unverified == "drop_email")):
                checks[i].append(f"email removed ({st})")
                email = None
            out.append(c.model_copy(update={
                "email": email,
                "email_status": st,
                "email_checks": checks[i],
                "verified": email is not None and st == "valid",
            }))
        return ProviderResult(contacts=out, source_failures=failures,
                              search_coverage=res.search_coverage, cost_cents=cost)


def _code(payload) -> str | None:
    return payload.get("code") if isinstance(payload, dict) else None


def _msg(payload) -> str:
    if isinstance(payload, dict):
        m = payload.get("message") or payload.get("error")
        if isinstance(m, dict):
            m = m.get("message")
        if m:
            return f": {str(m)[:160]}"
    return ""

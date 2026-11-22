"""People Data Labs implementation of `EnrichmentProvider` (Person Search API, v5).

Behavior measured with a free-plan key, not assumed:

- `POST https://api.peopledatalabs.com/v5/person/search` with an `X-Api-Key` header and an
  Elasticsearch-style `query` (bool, term, match_phrase are accepted).
- One credit per profile returned. The response headers report what the call spent
  (`x-call-credits-spent`) and what is left (`x-totallimit-remaining`).
- On the free plan, contact fields (work_email, emails, mobile_phone...) come back as
  `true` / `false` ("exists"), not as values. Names, job titles, LinkedIn URL and job-change
  dates do come back. This adapter only keeps a work email when it is an actual string.
- `job_title` behaves as an exact (keyword) field: `match_phrase` "sales operations" only
  finds people titled exactly that. Wildcards (`*sales*operations*`) find real titles such as
  "senior director, revenue operations"; titles are stored lowercase.
- The free plan allows 10 requests per minute; above that PDL answers 429 and spends nothing.
  This adapter spaces its calls and retries a 429 after the window.

One call per account: the people at the account's domain whose job title contains any of
the personas' `titles_any_of` phrases (words in order, anything in between), optionally
restricted to some `job_title_levels`, capped at `profiles_per_account`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from ..context import BuyerPersona
from ..models import Account, Confidence, ContactProposal, SourceAttribution
from .base import DiscoveryProvider, EnrichmentProvider, ProviderResult

SEARCH_URL = "https://api.peopledatalabs.com/v5/person/search"
COMPANY_SEARCH_URL = "https://api.peopledatalabs.com/v5/company/search"
# Published per-record price of PDL's first paid tier ($0.28). Used to express credits in
# the workflow's cents budget, including for free credits, so the cap stays meaningful.
DEFAULT_PRICE_CENTS_PER_PROFILE = 28

Transport = Callable[[str, dict, bytes], tuple[int, object, dict]]


def urllib_transport(url: str, headers: dict, body: bytes):
    req = urllib.request.Request(url, method="POST", headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, _parse(r.read()), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read()), {k.lower(): v for k, v in (e.headers or {}).items()}


def _parse(raw: bytes):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode(errors="replace")[:300]


class _PDLHttp:
    """Shared PDL call handling: spacing to the free-plan rate, one retry after a 429,
    credits read from the response headers."""

    def _init_http(self, api_key, min_interval_s, retry_after_429_s, transport, sleep, clock):
        if not api_key:
            raise ValueError("PDL API key is empty")
        self._api_key = api_key
        self.min_interval_s = min_interval_s
        self.retry_after_429_s = retry_after_429_s
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self.credits_remaining: int | None = None

    def _post(self, url: str, query: dict):
        body = json.dumps(query).encode()
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json"}
        for attempt in range(2):
            if self._last_call is not None:
                wait = self.min_interval_s - (self._clock() - self._last_call)
                if wait > 0:
                    self._sleep(wait)
            self._last_call = self._clock()
            status, payload, resp_headers = self._transport(url, headers, body)
            if status != 429 or attempt == 1:
                break
            self._sleep(self.retry_after_429_s)
        remaining = _int(resp_headers.get("x-totallimit-remaining"))
        if remaining is not None:
            self.credits_remaining = remaining
        return status, payload, _int(resp_headers.get("x-call-credits-spent"))


class PDLEnrichmentProvider(_PDLHttp, EnrichmentProvider):
    name = "peopledatalabs"

    def __init__(
        self,
        api_key: str,
        personas: Iterable[BuyerPersona],
        *,
        profiles_per_account: int = 2,
        price_cents_per_profile: int = DEFAULT_PRICE_CENTS_PER_PROFILE,
        title_levels: Iterable[str] | None = None,
        min_interval_s: float = 6.0,
        retry_after_429_s: float = 61.0,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._init_http(api_key, min_interval_s, retry_after_429_s, transport, sleep, clock)
        self.personas = list(personas)
        self.profiles_per_account = profiles_per_account
        self.price_cents_per_profile = price_cents_per_profile
        self.title_levels = [x.lower() for x in title_levels] if title_levels else []
        # Most one call can cost: every requested profile returned.
        self.cost_cents_per_call = profiles_per_account * price_cents_per_profile

    def build_query(self, domain: str, persona_names: Iterable[str]) -> dict:
        wanted = set(persona_names)
        phrases = sorted({
            t.lower() for p in self.personas if p.name in wanted for t in p.titles_any_of
        })
        if not phrases:
            raise ValueError("No persona titles to search for: set titles_any_of in the context")
        must = [
            {"term": {"job_company_website": domain}},
            # a bool with only `should` clauses requires at least one match
            {"bool": {"should": [
                {"wildcard": {"job_title": "*" + "*".join(t.split()) + "*"}} for t in phrases
            ]}},
        ]
        if self.title_levels:
            must.append({"terms": {"job_title_levels": self.title_levels}})
        return {"query": {"bool": {"must": must}}, "size": self.profiles_per_account}

    def enrich(self, account: Account, personas: Iterable[str]) -> ProviderResult:
        persona_names = list(personas)
        try:
            status, payload, spent = self._post(SEARCH_URL, self.build_query(account.domain, persona_names))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return ProviderResult(source_failures=[f"{self.name}:{account.domain}:{type(e).__name__}"],
                                  cost_cents=0)
        cost = None if spent is None else spent * self.price_cents_per_profile

        if status == 404:  # PDL answers "no records found" with a 404
            return ProviderResult(search_coverage={self.name: 0}, cost_cents=cost or 0)
        if status >= 300:
            message = payload.get("error", {}).get("message") if isinstance(payload, dict) else payload
            return ProviderResult(
                source_failures=[f"{self.name}:{account.domain}:{status}:{str(message)[:120]}"],
                cost_cents=cost or 0,
            )

        records = (payload or {}).get("data", []) if isinstance(payload, dict) else []
        contacts = [self._to_contact(account, r, persona_names) for r in records]
        if cost is None:
            cost = len(records) * self.price_cents_per_profile
        return ProviderResult(contacts=contacts, search_coverage={self.name: len(contacts)},
                              cost_cents=cost)

    def _to_contact(self, account: Account, r: dict, persona_names: list[str]) -> ContactProposal:
        title = r.get("job_title") or ""
        email = r.get("work_email")
        return ContactProposal(
            account_id=account.account_id,
            persona=self._persona_for(title, persona_names),
            first_name=_title_case(r.get("first_name")),
            last_name=_title_case(r.get("last_name")),
            title=_title_case(title) or None,
            # The free plan returns `true` instead of the address: keep real strings only.
            email=email if isinstance(email, str) and "@" in email else None,
            verified=False,
            source=SourceAttribution(
                provider=self.name,
                url=_linkedin(r.get("linkedin_url")),
                observed_at=datetime.now(UTC),
            ),
        )

    def _persona_for(self, title: str, persona_names: list[str]) -> str:
        """Match phrases with the query's rule (words in order, anything in between). In a
        job title the function comes last, so the match ending furthest right wins, then
        the longest phrase: "vp of sales operations" is Revenue Operations, not leadership."""
        t = title.lower()
        best, best_key = None, (-1, -1)
        for p in self.personas:
            if p.name not in persona_names:
                continue
            for phrase in p.titles_any_of:
                words = phrase.lower().split()
                end = _match_end(words, t)
                if end >= 0 and (end, len(phrase)) > best_key:
                    best, best_key = p.name, (end, len(phrase))
        return best or (persona_names[0] if persona_names else "unknown")


COUNTRY_NAMES = {"US": "united states", "USA": "united states", "GB": "united kingdom",
                 "UK": "united kingdom", "FR": "france", "DE": "germany", "CA": "canada"}
COUNTRY_CODES = {v: k for k, v in COUNTRY_NAMES.items() if len(k) == 2}


class PDLCompanyDiscoveryProvider(_PDLHttp, DiscoveryProvider):
    """Discovers companies with PDL's Company Search API (1 credit per company returned,
    from a quota separate from Person Search on the free plan).

    The query applies the ICP band (employee range, geographies) plus the provider-specific
    `discovery.pdl_industries` / `discovery.pdl_tags` from the context. Records are mapped to
    `Account` as observed: `employee_count` is PDL's estimate from the profiles it holds,
    so it is labeled `secondary`, never `verified`.
    """

    name = "peopledatalabs-company"

    def __init__(
        self,
        api_key: str,
        context,
        *,
        min_interval_s: float = 6.0,
        retry_after_429_s: float = 61.0,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._init_http(api_key, min_interval_s, retry_after_429_s, transport, sleep, clock)
        self.context = context

    def build_query(self, limit: int) -> dict:
        icp, d = self.context.icp, self.context.discovery
        must: list[dict] = [{"range": {"employee_count": {"gte": icp.employees_min, "lte": icp.employees_max}}}]
        countries = [COUNTRY_NAMES.get(g.upper(), g.lower()) for g in icp.geographies]
        if countries:
            must.append({"terms": {"location.country": countries}})
        if d.pdl_industries:
            must.append({"terms": {"industry": [x.lower() for x in d.pdl_industries]}})
        if d.pdl_tags:
            must.append({"terms": {"tags": [x.lower() for x in d.pdl_tags]}})
        must.append({"exists": {"field": "website"}})
        return {"query": {"bool": {"must": must}}, "size": min(limit, 100)}

    def discover(self, *, limit: int) -> ProviderResult:
        try:
            status, payload, _spent = self._post(COMPANY_SEARCH_URL, self.build_query(limit))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return ProviderResult(source_failures=[f"{self.name}:{type(e).__name__}"])
        if status == 404:
            return ProviderResult(search_coverage={self.name: 0})
        if status >= 300:
            message = payload.get("error", {}).get("message") if isinstance(payload, dict) else payload
            return ProviderResult(source_failures=[f"{self.name}:{status}:{str(message)[:120]}"])
        records = (payload or {}).get("data", []) if isinstance(payload, dict) else []
        accounts = [a for a in (self._to_account(r) for r in records) if a is not None]
        coverage = {self.name: len(accounts)}
        total = (payload or {}).get("total") if isinstance(payload, dict) else None
        if isinstance(total, int):
            coverage[f"{self.name}:total_matching"] = total
        return ProviderResult(accounts=accounts, search_coverage=coverage)

    def _to_account(self, r: dict) -> Account | None:
        website = r.get("website")
        if not website:
            return None
        loc = r.get("location") or {}
        tags = [t.lower() for t in (r.get("tags") or [])]
        industry = r.get("industry") or ""
        # The ICP keywords speak "saas"; PDL says it with a tag. Keep both, visibly.
        label = industry + ("; saas" if "software as a service" in tags else "")
        country = (loc.get("country") or "").lower()
        missing = [f for f, v in (("employees", r.get("employee_count")), ("industry", industry)) if not v]
        return Account(
            account_id=f"pdl:{r.get('id') or website}",
            domain=website,
            name=(r.get("display_name") or r.get("name") or website).strip(),
            employees=r.get("employee_count"),
            employees_confidence=Confidence.SECONDARY if r.get("employee_count") else Confidence.UNKNOWN,
            industry=label or None,
            hq_country=COUNTRY_CODES.get(country, country.upper() or None),
            hq_state=loc.get("region"),
            source=SourceAttribution(
                provider=self.name,
                url=_linkedin(r.get("linkedin_url")),
                observed_at=datetime.now(UTC),
            ),
            fields_missing=missing,
        )


def _match_end(words: list[str], text: str) -> int:
    """End index of the words found in order in text, or -1."""
    pos = 0
    for w in words:
        i = text.find(w, pos)
        if i < 0:
            return -1
        pos = i + len(w)
    return pos


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _title_case(v):
    return v.title() if isinstance(v, str) else v


def _linkedin(v):
    if not isinstance(v, str) or not v:
        return None
    return v if v.startswith("http") else f"https://{v}"

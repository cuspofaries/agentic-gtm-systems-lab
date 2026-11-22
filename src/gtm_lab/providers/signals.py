"""Dated buying signals from the web (Exa Search API).

One search per account, limited to the freshness window of `signal_policy`. Each result
becomes a candidate `Signal` with its publication date and source URL, then goes through
checks the code applies, not the model or the search engine:

- **About the company.** The page is on the company's domain, or names its domain, or
  has its full name in the title. A search for "Acme" also returns people and pages
  unrelated to Acme, and pages that list Acme among others; those are rejected.
- **Not a personal profile.** Pages such as `linkedin.com/in/...` are people, not company
  events, and carry personal data; they are rejected. Their date is a crawl date, not the
  date of an event.
- **Dated, inside the window.** An undated page or one outside the window is rejected.

What survives is labeled `secondary`: a web page reports an event, it does not prove it.
The date is the page's publication date, which can differ from the event's.

Every rejection is counted by reason, so a reviewer sees what was dropped and why.

Exa facts used here (exa.ai/docs, 2026-11-22): `POST /search`, `x-api-key` header,
`type: "lite"` at $4 per 1,000 requests with up to 10 results, `startPublishedDate`,
`contents.highlights` at no extra cost, and `costDollars.total` in every response
(measured: 0.004 for one `lite` search).
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from ..models import Account, Confidence, Signal, SourceAttribution

EXA_SEARCH_URL = "https://api.exa.ai/search"
# `lite` costs $4 per 1,000 requests with up to 10 results: 4 thousandths of a dollar.
EXA_LITE_MILLIDOLLARS = 4
MAX_RESULTS = 10  # above 10, Exa bills each extra result

# (method, url, headers, body) -> (status, parsed JSON or None)
Transport = Callable[[str, str, dict, bytes | None], tuple[int, object]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


@dataclass
class SignalResult:
    signals: list[Signal] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)  # reason -> count
    cost_millidollars: int = 0
    failures: list[str] = field(default_factory=list)


class SignalProvider(ABC):
    name: str = "signals"
    # Most one call can cost, in thousandths of a dollar. Checked before each call.
    max_cost_millidollars_per_call: int = 0

    @abstractmethod
    def find(self, account: Account, *, max_age_days: int) -> SignalResult: ...


# --- classification ----------------------------------------------------------------

_LEADER = (r"(chief revenue officer|chief sales officer|\bcro\b|(s?vp|vice president),? (of )?(sales|revenue)"
           r"|head of (sales|revenue)|sales leader)")
_APPOINT = r"(appoint|names|named|hires|hired|joins|joined|promot|welcomes|announces .{0,40}as)"
_SALES_ROLE = (r"(account executive|account manager|sales development|\bsdr\b|\bbdr\b|sales manager"
               r"|sales director|enterprise sales|inside sales|sales representative|business development"
               r"|sales engineer|revenue operations)")
_JOB = r"(hiring|job|jobs|apply|vacanc|career|opening|position|we're looking|we are looking)"
_JOB_HOSTS = ("greenhouse.io", "lever.co", "workable.com", "ashbyhq.com", "indeed.com", "glassdoor.com")
_EXPANSION = (r"(raises|raised|funding|series [a-f]\b|investment|acquires|acquired|acquisition"
              r"|expands|expansion|new office|opens|launches in)")


_INDEX_PAGES = {"", "news", "newsroom", "press", "press-releases", "blog", "media", "about", "careers", "jobs"}


def _index_page(url: str) -> bool:
    """A section front page or a company profile: it lists things, it is not one event."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.lower().split("/") if x]
    if host.endswith("linkedin.com") and len(parts) <= 2 and parts[:1] == ["company"]:
        return True
    return len(parts) <= 1 and (parts[0] if parts else "") in _INDEX_PAGES


def classify(text: str, url: str) -> str:
    """Kind of event a page reports. A page that is not about one event is `other`."""
    if _index_page(url):
        return "other"
    t = text.lower()
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path.lower()
    if re.search(_LEADER, t) and re.search(_APPOINT, t):
        return "new_sales_leadership"
    jobish = re.search(_JOB, t) or any(host.endswith(h) for h in _JOB_HOSTS) or re.search(
        r"/(jobs?|careers?|vacanc\w*)/", path)
    if re.search(_SALES_ROLE, t) and jobish:
        return "sales_hiring"
    if re.search(_EXPANSION, t):
        return "revenue_expansion"
    return "other"


# --- checks ------------------------------------------------------------------------

_SUFFIXES = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|llc|ltd|limited|co|company|plc|gmbh|sa)\.?$",
                       re.I)


def _anchors(account: Account) -> list[str]:
    """The full company name (legal suffix removed) and its domain.

    Not the domain's first label: on a real run "brightpath" from brightpath.example
    matched pages about a different BrightPath company.
    """
    name = _SUFFIXES.sub("", account.name.strip()).lower()
    out = [account.domain.lower()]
    if len(name) >= 3:
        out.append(name)
    return out


def _word(a: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", text.lower()) is not None


def _about(account: Account, title: str, excerpt: str, url: str) -> str | None:
    """None if the page is about the company, else the rejection reason.

    About the company: the page is on its domain, or its domain appears anywhere, or its
    name appears in the title. A name found only in the body is a page that mentions the
    company among others (measured: a vendor's use-case page listing several companies).
    """
    domain = account.domain.lower()
    host = (urlparse(url).hostname or "").lower()
    if host == domain or host.endswith("." + domain) or _word(domain, f"{title} {excerpt}"):
        return None
    names = [a for a in _anchors(account) if a != domain]
    if any(_word(n, title) for n in names):
        return None
    if any(_word(n, excerpt) for n in names):
        return "mentioned_in_passing"
    return "not_about_company"


def _personal_profile(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return host.endswith("linkedin.com") and p.path.lower().startswith(("/in/", "/pub/"))


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# --- Exa -----------------------------------------------------------------------------


class ExaSignalProvider(SignalProvider):
    name = "exa"

    def __init__(
        self,
        api_key: str,
        *,
        max_results: int = 5,
        transport: Transport = urllib_transport,
        min_interval_s: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if not api_key:
            raise ValueError("Exa API key is empty")
        if not 1 <= max_results <= MAX_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS}")
        self._key = api_key
        self.max_results = max_results
        self._transport, self._sleep, self._clock, self._now = transport, sleep, clock, now
        self.min_interval_s = min_interval_s
        self._last: float | None = None
        self.max_cost_millidollars_per_call = EXA_LITE_MILLIDOLLARS

    @staticmethod
    def query(account: Account) -> str:
        return (f"{account.name} ({account.domain}) news: hiring for sales roles, new sales leader "
                f"appointed, funding, acquisition or expansion")

    def find(self, account: Account, *, max_age_days: int) -> SignalResult:
        result = SignalResult()
        now = self._now()
        since = (now - timedelta(days=max_age_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        body = {
            "query": self.query(account),
            "type": "lite",
            "numResults": self.max_results,
            "startPublishedDate": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "contents": {"highlights": True},
        }
        if self._last is not None:
            wait = self.min_interval_s - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        headers = {"x-api-key": self._key, "Content-Type": "application/json"}
        try:
            status, payload = self._transport("POST", EXA_SEARCH_URL, headers, json.dumps(body).encode())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            result.failures.append(f"exa:{account.domain}:{type(e).__name__}")
            return result
        if not isinstance(payload, dict) or status >= 300:
            msg = payload.get("error") if isinstance(payload, dict) else None
            result.failures.append(f"exa:{account.domain}:HTTP {status}" + (f": {str(msg)[:120]}" if msg else ""))
            return result

        cost = (payload.get("costDollars") or {}).get("total")
        # Charge what Exa reports; without a figure, the announced maximum.
        result.cost_millidollars = (math.ceil(round(float(cost) * 1000, 6)) if cost is not None
                                    else self.max_cost_millidollars_per_call)

        for r in payload.get("results") or []:
            url = r.get("url") or ""
            title = r.get("title") or ""
            excerpt = " ".join(h for h in (r.get("highlights") or []) if isinstance(h, str))
            text = f"{title} {excerpt}"
            date = _parse_date(r.get("publishedDate"))
            if _personal_profile(url):
                result.rejected["personal_profile"] += 1
            elif (why := _about(account, title, excerpt, url)) is not None:
                result.rejected[why] += 1
            elif date is None:
                result.rejected["undated"] += 1
            elif not (since <= date <= now + timedelta(days=1)):
                result.rejected["outside_window"] += 1
            else:
                result.signals.append(Signal(
                    kind=classify(text, url),
                    description=(title or excerpt)[:200].strip(),
                    event_date=date,
                    source=SourceAttribution(provider=self.name, url=url, observed_at=now),
                    confidence=Confidence.SECONDARY,
                ))
        return result

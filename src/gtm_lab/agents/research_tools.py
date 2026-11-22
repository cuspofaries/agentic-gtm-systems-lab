"""Shared machinery for bounded research loops: tools, budgets, provenance, the loop.

Used by `ResearchQualifier` (missing company facts) and `OpportunityInvestigator`
(product-specific opportunity). Claude chooses the tool calls; this module decides what
a call may do and what it costs:

- `search(query, purpose)`: Exa `lite`. The code picks the date window from `purpose`:
  company facts search `research_policy.company_facts_max_age_days` back, recent events
  `signal_policy.max_signal_age_days`.
- `read(url)`: Exa Contents. Only URLs a search returned in this session (or pages given
  to the session up front) can be read; anything else is refused without a network call.
- Every page read gets an id (`page-1`, ...). Quotes are checked against the text of the
  page actually read, never against a search excerpt.
- Budgets are per run and checked before every call: LLM cents against the worst case
  of the next turn, Exa thousandths of a dollar against the call's maximum.
- Each tool call is kept in the session trace (tool, input, outcome, cost).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from ..context import GTMContext
from ..models import Account
from ..providers import signals as exa
from .anthropic_provider import CHARS_PER_TOKEN_LOW, DEFAULT_MODEL, PRICE_PER_MTOK

EXA_CONTENTS_URL = "https://api.exa.ai/contents"
EXA_CONTENTS_MILLIDOLLARS = 1  # $1 per 1,000 pages
PAGE_CHARS = 6000  # text kept per page read
MAX_OUTPUT_TOKENS = 2000  # per turn; thinking counts toward it

SEARCH_TOOL = {
    "name": "search",
    "description": ("Search the web. `purpose` sets how far back: company_facts (headcount, location, "
                    "industry) searches about two years back, recent_events only the recent window. "
                    "Returns up to 5 results with URL, title, publication date and a short excerpt."),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language query."},
            "purpose": {"type": "string", "enum": ["company_facts", "recent_events"]},
        },
        "required": ["query", "purpose"],
        "additionalProperties": False,
    },
}
READ_TOOL = {
    "name": "read",
    "description": "Read the text of one page returned by a previous search. Quotes must come from pages you read.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    },
}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _dump(obj: Any):
    return obj.model_dump() if hasattr(obj, "model_dump") else str(obj)


def as_params(content: list) -> list[dict]:
    """The assistant turn as plain request blocks, whatever object the client returned."""
    out = []
    for b in content:
        if b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type == "text" and b.text:
            out.append({"type": "text", "text": b.text})
    return out


def _safe_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.hostname) and " " not in url


@dataclass
class ResearchBudget:
    """Run-wide caps and spend, shared by every account the loop researches."""

    max_llm_cents: int = 50
    max_exa_millidollars: int = 50
    model: str = DEFAULT_MODEL
    llm_spent_cents: int = 0
    exa_spent_millidollars: int = 0
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def worst_case_cents(self, system: str, tools: list, messages: list) -> int:
        chars = len(system) + len(json.dumps(tools)) + len(json.dumps(messages, default=_dump))
        price_in, price_out = PRICE_PER_MTOK.get(self.model, PRICE_PER_MTOK[DEFAULT_MODEL])
        usd = (math.ceil(chars / CHARS_PER_TOKEN_LOW) * price_in + MAX_OUTPUT_TOKENS * price_out) / 1_000_000
        return math.ceil(usd * 100)

    def charge_llm(self, usage: Any) -> None:
        price_in, price_out = PRICE_PER_MTOK.get(self.model, PRICE_PER_MTOK[DEFAULT_MODEL])
        usd = (getattr(usage, "input_tokens", 0) * price_in
               + getattr(usage, "output_tokens", 0) * price_out) / 1_000_000
        self.llm_spent_cents += math.ceil(usd * 100)


@dataclass
class Page:
    ref: str
    url: str
    title: str
    text: str
    published_at: datetime | None


class ResearchSession:
    """Tools, pages and trace for one account."""

    def __init__(self, account: Account, ctx: GTMContext, budget: ResearchBudget, *,
                 exa_key: str, exa_transport: Callable, now: datetime,
                 known_pages: list[dict] | None = None):
        self.account, self.budget = account, budget
        self._key, self._transport = exa_key, exa_transport
        self.windows = {
            "company_facts": now - timedelta(days=ctx.research_policy.company_facts_max_age_days),
            "recent_events": now - timedelta(days=ctx.signal_policy.max_signal_age_days),
        }
        self.found: dict[str, dict] = {}  # url -> search result: what `read` may open
        self.pages: dict[str, Page] = {}  # url -> page read
        self.trace: list[dict] = []
        for r in known_pages or []:  # e.g. pages a signal search already returned this run
            if r.get("url"):
                self.found[r["url"]] = r

    # --- tools ------------------------------------------------------------------------

    def run(self, name: str, args: dict) -> str:
        if name == "search":
            purpose = args.get("purpose")
            if purpose not in self.windows:
                self.trace.append({"tool": "search", "input": args, "outcome": "invalid_purpose"})
                return "Invalid purpose."
            return self.search(str(args.get("query", ""))[:300], purpose)
        if name == "read":
            return self.read(str(args.get("url", ""))[:2000])
        self.trace.append({"tool": name, "outcome": "unknown_tool"})
        return "Unknown tool."

    def _exa(self, url: str, body: dict, max_cost: int, tool: str, arg: str):
        if self.budget.exa_spent_millidollars + max_cost > self.budget.max_exa_millidollars:
            self.trace.append({"tool": tool, "input": arg, "outcome": "exa_budget_exceeded"})
            return None, "Search budget exhausted: no more searches or reads. Submit your answer."
        headers = {"x-api-key": self._key, "Content-Type": "application/json"}
        try:
            status, payload = self._transport("POST", url, headers, json.dumps(body).encode())
        except OSError as e:
            self.trace.append({"tool": tool, "input": arg, "outcome": f"error {type(e).__name__}"})
            return None, "The tool failed."
        if status >= 300 or not isinstance(payload, dict):
            self.trace.append({"tool": tool, "input": arg, "outcome": f"HTTP {status}"})
            return None, f"The tool failed (HTTP {status})."
        cost = (payload.get("costDollars") or {}).get("total")
        spent = math.ceil(round(float(cost) * 1000, 6)) if cost is not None else max_cost
        self.budget.exa_spent_millidollars += spent
        return (payload, spent), None

    def search(self, query: str, purpose: str) -> str:
        since = self.windows[purpose]
        body = {"query": query, "type": "lite", "numResults": 5,
                "startPublishedDate": since.astimezone(UTC).strftime("%Y-%m-%dT00:00:00.000Z"),
                "contents": {"highlights": True}}
        got, err = self._exa(exa.EXA_SEARCH_URL, body, exa.EXA_LITE_MILLIDOLLARS, "search", query)
        if err:
            return err
        payload, spent = got
        lines = []
        for r in payload.get("results") or []:
            url = r.get("url") or ""
            if not _safe_url(url):
                continue
            self.found[url] = r
            excerpt = " ".join(h for h in (r.get("highlights") or []) if isinstance(h, str))[:400]
            lines.append(f"<result url={json.dumps(url)} date={json.dumps(r.get('publishedDate'))}>"
                         f"{r.get('title') or ''}\n{excerpt}</result>")
        self.trace.append({"tool": "search", "input": query, "purpose": purpose,
                           "outcome": f"{len(lines)} results", "cost_millidollars": spent})
        return "\n".join(lines) or "No results."

    def read(self, url: str) -> str:
        if url not in self.found or not _safe_url(url):
            self.trace.append({"tool": "read", "input": url, "outcome": "refused_not_from_search"})
            return "Only URLs returned by a search in this research can be read."
        if url in self.pages:
            p = self.pages[url]
            return f"<page id={json.dumps(p.ref)} url={json.dumps(url)}>{p.text}</page>"
        body = {"urls": [url], "text": {"maxCharacters": PAGE_CHARS}}
        got, err = self._exa(EXA_CONTENTS_URL, body, EXA_CONTENTS_MILLIDOLLARS, "read", url)
        if err:
            return err
        payload, spent = got
        text = next((r.get("text") or "" for r in payload.get("results") or [] if r.get("text")), "")
        if not text:
            self.trace.append({"tool": "read", "input": url, "outcome": "no_text", "cost_millidollars": spent})
            return "The page has no readable text."
        r = self.found[url]
        ref = f"page-{len(self.pages) + 1}"
        self.pages[url] = Page(ref, url, r.get("title") or "", text[:PAGE_CHARS], exa._parse_date(r.get("publishedDate")))
        self.trace.append({"tool": "read", "input": url, "page": ref,
                           "outcome": f"{len(self.pages[url].text)} chars", "cost_millidollars": spent})
        return f"<page id={json.dumps(ref)} url={json.dumps(url)}>{self.pages[url].text}</page>"

    # --- checks -----------------------------------------------------------------------

    def check_quote(self, url: str, quote: str) -> tuple[str, Page | None]:
        """quote_found / page_not_read / quote_not_found / subject_mismatch."""
        page = self.pages.get(url)
        if page is None:
            return "page_not_read", None
        if len(norm(quote)) < 8 or norm(quote) not in norm(page.text):
            return "quote_not_found", page
        # The page must be about this company: on its domain, naming its domain, its name in
        # the title, or its name in the quote itself.
        names = [a for a in exa._anchors(self.account) if a != self.account.domain.lower()]
        in_quote = any(exa._word(n, quote) for n in names) or exa._word(self.account.domain.lower(), quote)
        if exa._about(self.account, page.title, page.text, url) is not None and not in_quote:
            return "subject_mismatch", page
        return "quote_found", page


def run_loop(*, client: Any, budget: ResearchBudget, system: str, user: str, tools: list,
             session: ResearchSession, max_steps: int,
             on_submit: Callable[[dict, int], str | None]) -> tuple[str, Any]:
    """Run Claude with `tools` until it calls `submit`, or a bound stops it.

    `on_submit(args, steps_used)` returns None to accept the answer, or an error message
    to send back as a tool error (the caller decides when a resubmission is allowed).
    Returns ("submitted", args) or ("stopped", reason).
    """
    messages: list[dict] = [{"role": "user", "content": user}]
    steps = 0
    while True:
        worst = budget.worst_case_cents(system, tools, messages)
        if budget.llm_spent_cents + worst > budget.max_llm_cents:
            return "stopped", "research_llm_budget_would_exceed"
        try:
            response = client.messages.create(
                model=budget.model, max_tokens=MAX_OUTPUT_TOKENS, system=system,
                tools=tools, messages=messages, output_config={"effort": "low"},
            )
        except Exception as e:  # API, connection or credential errors: fail closed
            budget.errors.append(f"research_llm_error: {type(e).__name__}")
            return "stopped", "research_llm_error"
        budget.llm_calls += 1
        budget.charge_llm(response.usage)
        if response.stop_reason == "refusal":
            return "stopped", "research_llm_refused"
        if response.stop_reason == "max_tokens":
            return "stopped", "research_output_truncated"
        uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            return "stopped", "research_no_decision"
        messages.append({"role": "assistant", "content": as_params(response.content)})
        results = []
        for use in uses:
            steps += 1
            if steps > max_steps:
                return "stopped", "research_step_cap_reached"
            if use.name == "submit":
                problem = on_submit(use.input, steps)
                if problem is None:
                    return "submitted", use.input
                session.trace.append({"tool": "submit", "outcome": "sent_back", "reason": problem[:200]})
                results.append({"type": "tool_result", "tool_use_id": use.id, "is_error": True,
                                "content": f"{problem} {max_steps - steps} tool call(s) left."})
                continue
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": session.run(use.name, use.input)})
        messages.append({"role": "user", "content": results})

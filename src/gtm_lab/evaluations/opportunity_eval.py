"""Baseline vs agent on hand-labeled opportunity cases (`evals/opportunity/cases.yaml`).

Both assessors get the same inputs: the account, its ICP decision, its signals, and a
fictional web (`search` returns the case's results, `read` the page text). The baseline
uses only the signal kinds; the agent may search, read and abstain.

Claude's answers come from a cassette: `--record` calls the API once and stores every
turn (fictional data only, no key); the default replays them, offline and free, so the
numbers below are reproducible in CI. A replay that meets a request it has not recorded
fails instead of calling the network.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import yaml

from ..agents.opportunity import OpportunityInvestigator, baseline_assessment
from ..agents.research_tools import EXA_CONTENTS_URL, ResearchBudget
from ..context import GTMContext
from ..models import (
    Account,
    Confidence,
    OpportunityAssessment,
    QualificationDecision,
    Signal,
    SourceAttribution,
)
from ..providers.signals import EXA_SEARCH_URL

EVAL_NOW = datetime(2026, 11, 22, tzinfo=UTC)
CASE_LLM_CENTS = 15  # cap per case
CASE_EXA_MILLIDOLLARS = 20


# --- inputs ----------------------------------------------------------------------------


def load_cases(path: Path) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(Path(path).read_text())
    return data.get("defaults", {}), data["cases"]


def _date(days_ago):
    return None if days_ago is None else EVAL_NOW - timedelta(days=days_ago)


def build_inputs(case: dict, defaults: dict) -> tuple[Account, QualificationDecision]:
    c = {**defaults, **case}
    signals = [Signal(kind=s["kind"], description=s["title"], event_date=_date(s.get("days_ago")),
                      source=SourceAttribution(provider="exa", url=s.get("url")))
               for s in c.get("signals") or []]
    acc = Account(account_id=c["id"], domain=c["domain"], name=c["name"], employees=c.get("employees"),
                  employees_confidence=Confidence.SECONDARY if c.get("employees") else Confidence.UNKNOWN,
                  industry=c.get("industry"), hq_country=c.get("hq_country"), signals=signals,
                  signal_search=c.get("signal_search", "found" if signals else "no_result"),
                  fields_missing=[] if c.get("employees") else ["employees"],
                  source=SourceAttribution(provider="eval-fixture"))
    dec = QualificationDecision(account_id=c["id"], decision=c.get("icp", "qualified"),
                                explanation="ICP decision given by the case", confidence=0.8)
    return acc, dec


def _page_text(p: dict) -> str:
    if "text_repeat" in p:
        r = p["text_repeat"]
        return r["text"] * int(r["times"]) + p.get("text_after", "")
    return p.get("text") or ""


class FictionalWeb:
    """Exa stand-in for one case.

    Query-insensitive (v1 cases): `search` returns every page. Query-sensitive (pages
    with `keywords` or `noise`): a page is returned only if the query names the company
    (domain, full name or first word of the name) and shares a word with its keywords;
    `noise` pages come back for any query naming the company. `search_fails` makes every
    search fail.
    """

    def __init__(self, case: dict):
        self.case = case
        self.pages = case.get("pages") or {}
        self.sensitive = any("keywords" in p or p.get("noise") for p in self.pages.values())
        urls = case.get("results")
        self.searches: list[tuple[str, list[str]]] = []
        self.reads: list[str] = []
        self.results = [
            {"url": u, "title": p.get("title", ""),
             "publishedDate": (_date(p.get("days_ago")).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                               if p.get("days_ago") is not None else None),
             "highlights": [p.get("excerpt") or _page_text(p)[:160]]}
            for u, p in self.pages.items() if urls is None or u in urls]

    def _names_company(self, q: str) -> bool:
        name = self.case["name"].lower()
        first = name.split()[0]
        return self.case["domain"].lower() in q or name in q or (len(first) >= 4 and first in q)

    def _matches(self, query: str) -> list[dict]:
        if not self.sensitive:
            return self.results
        q = query.lower()
        if not self._names_company(q):
            return []
        words = set(re.findall(r"[a-z0-9]+", q))
        keep = []
        for r in self.results:
            p = self.pages[r["url"]]
            if p.get("noise") or words & {k.lower() for k in p.get("keywords", [])}:
                keep.append(r)
        return keep

    def __call__(self, method, url, headers, body):
        req = json.loads(body)
        if url == EXA_SEARCH_URL:
            if self.case.get("search_fails"):
                return 503, {"error": "search unavailable"}
            found = self._matches(req.get("query", ""))
            self.searches.append((req.get("query", ""), [r["url"] for r in found]))
            return 200, {"results": found, "costDollars": {"total": 0.004}}
        if url == EXA_CONTENTS_URL:
            page = self.pages.get(req["urls"][0])
            self.reads.append(req["urls"][0])
            if page is None or page.get("fail"):
                return 502, {"error": "fetch failed"}
            return 200, {"results": [{"url": req["urls"][0], "text": _page_text(page)}],
                         "costDollars": {"total": 0.001}}
        return 404, None


class LLMCassette:
    """Records or replays `messages.create`, keyed by case, turn and the request's last message."""

    def __init__(self, path: Path, mode: str, real_client=None):
        self.path, self.mode, self.real = Path(path), mode, real_client
        # replay: recorded turns only. record: every turn from the API (a new cassette).
        # extend: recorded turns replayed, missing ones called and added (after a code change
        # that alters some conversations).
        self.data: dict = (json.loads(self.path.read_text()) if mode in ("replay", "extend") and self.path.exists()
                           else {"turns": {}})
        self.case_id, self.turn, self.misses, self.added = "", 0, [], 0

    def for_case(self, case_id: str):
        self.case_id, self.turn = case_id, 0
        return SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.turn += 1
        last = json.dumps(kw["messages"][-1]["content"], sort_keys=True, default=str)
        # The prompt and tool schemas are part of the key: after a prompt change, an old
        # recording must miss, not replay answers given to a different prompt.
        agent = json.dumps({"model": kw.get("model"), "system": kw.get("system"), "tools": kw.get("tools")},
                           sort_keys=True, default=str)
        fp = hashlib.sha256(agent.encode()).hexdigest()[:12]
        key = f"{self.case_id}#{self.turn}#{fp}#{hashlib.sha256(last.encode()).hexdigest()[:16]}"
        if self.mode == "replay" or (self.mode == "extend" and key in self.data["turns"]):
            if key not in self.data["turns"]:
                self.misses.append(key)
                raise LookupError(f"not recorded: {key}")
            d = self.data["turns"][key]
        else:
            self.added += 1
            r = self.real.messages.create(**kw)
            d = {"content": [({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input} if b.type == "tool_use"
                              else {"type": "text", "text": getattr(b, "text", "")}) for b in r.content
                             if b.type in ("tool_use", "text")],
                 "stop_reason": r.stop_reason,
                 "usage": {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}}
            self.data["turns"][key] = d
        return SimpleNamespace(content=[SimpleNamespace(**b) for b in d["content"]], stop_reason=d["stop_reason"],
                               usage=SimpleNamespace(**d["usage"]))

    def save(self, model: str) -> None:
        self.data["recorded_at"] = datetime.now(UTC).isoformat()
        self.data["model"] = model
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))


class NaiveReader:
    """Control with no model: read the first signal page, quote its first sentence as support
    for the offer's first pain, run one recent-events search naming the company, and answer
    the semantic check by keyword (a supporting quote counts only if it contains one of the
    pain's evidence_terms). The same code checks apply. It is the conservative code-only
    path: what the result would be with lexical rules instead of reading."""

    def __init__(self, pain_id: str, evidence_terms: list[str]):
        self.pain_id, self.terms = pain_id, [t.lower() for t in evidence_terms]
        self.messages = SimpleNamespace(create=self._create)

    @staticmethod
    def _use(name, inp):
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id=f"naive-{name}", name=name, input=inp)],
                               stop_reason="tool_use", usage=SimpleNamespace(input_tokens=0, output_tokens=0))

    def _create(self, **kw):
        if kw["tools"][0]["name"] == "verdict":
            quote = kw["messages"][0]["content"].split("<quote>")[1].split("</quote>")[0].lower()
            return self._use("verdict", {"verdict": "expresses" if any(t in quote for t in self.terms)
                                         else "unrelated", "reason": "keyword rule"})
        msgs = kw["messages"]
        first = msgs[0]["content"]
        name = json.loads(first.split("<account_data>\n")[1].split("\n</account_data>")[0])["name"]
        if len(msgs) == 1:
            signals = json.loads(first.split("<result>")[1].split("</result>")[0])
            url = next((x["url"] for x in signals if x.get("url")), None)
            if url:
                return self._use("read", {"url": url})
        query = f"{name} hiring freeze layoffs"
        searched = any(isinstance(m["content"], list) and any(
            b.get("type") == "tool_use" and b.get("name") == "search" for b in m["content"] if isinstance(b, dict))
            for m in msgs)
        if not searched:
            self._page = next((m["content"][0]["content"] for m in msgs[1:] if isinstance(m["content"], list)
                               and m["content"] and isinstance(m["content"][0], dict)
                               and str(m["content"][0].get("content", "")).startswith("<page")), None)
            return self._use("search", {"query": query, "purpose": "recent_events"})
        page = getattr(self, "_page", None)
        obs = []
        if page:
            url = page.split('url="')[1].split('"')[0]
            text = page.split(">", 1)[1]
            obs = [{"event": "signal", "statement": "first sentence", "subject": "", "url": url,
                    "quote": text.split(".")[0][:200], "event_date_stated": "", "stance": "supports",
                    "pain_id": self.pain_id, "rationale": "naive"}]
        checks = [{"pain_id": self.pain_id, "alternative": "hiring freeze", "query": query,
                   "outcome": "no_contradiction_found", "observation_ids": []}]
        return self._use("submit", {"observations": obs, "disconfirmation_checks": checks,
                                    "alternative_explanations": [], "unknowns": [],
                                    "recommended_next_action": "review", "explanation": "naive control"})


def _naive(ctx) -> NaiveReader:
    pain = next(p for p in ctx.pain_points if p.id == ctx.offers[0].pains[0])
    return NaiveReader(pain.id, pain.evidence_terms)


# --- run -------------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: dict
    baseline: OpportunityAssessment
    checks_only: OpportunityAssessment
    agent: OpportunityAssessment
    agent_web: FictionalWeb | None = None


@dataclass
class EvalRun:
    results: list[CaseResult] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)


def run_eval(cases_path: Path, ctx: GTMContext, cassette: LLMCassette, *, model: str | None = None) -> EvalRun:
    defaults, cases = load_cases(cases_path)
    out = EvalRun()
    for case in cases:
        acc, dec = build_inputs(case, defaults)
        base = baseline_assessment(acc, dec, ctx, now=EVAL_NOW)
        budget = ResearchBudget(max_llm_cents=CASE_LLM_CENTS,
                                max_exa_millidollars=case.get("exa_budget_millidollars", CASE_EXA_MILLIDOLLARS),
                                **({"model": model} if model else {}))
        web = FictionalWeb(case)
        agent = OpportunityInvestigator("eval-fixture", client=cassette.for_case(case["id"]),
                                        exa_transport=web, now=lambda: EVAL_NOW, budget=budget,
                                        max_steps=ctx.research_policy.opportunity_max_steps)
        naive = OpportunityInvestigator(
            "eval-fixture", client=_naive(ctx), exa_transport=FictionalWeb(case),
            now=lambda: EVAL_NOW, max_steps=ctx.research_policy.opportunity_max_steps,
            budget=ResearchBudget(max_llm_cents=CASE_LLM_CENTS,
                                  max_exa_millidollars=case.get("exa_budget_millidollars", CASE_EXA_MILLIDOLLARS)))
        out.results.append(CaseResult(case, base, naive.assess(acc, dec, ctx), agent.assess(acc, dec, ctx), web))
    out.misses = list(cassette.misses)
    return out


# --- metrics ---------------------------------------------------------------------------

ABSTAIN = {"needs_research", "hypothesis_only", "insufficient_evidence"}


def _confusion(rows: list[tuple[bool | None, OpportunityAssessment]]) -> dict:
    lab = [(r, a) for r, a in rows if r is not None]
    tp = sum(1 for r, a in lab if r and a.assessment_status == "evidence_backed")
    fp = sum(1 for r, a in lab if not r and a.assessment_status == "evidence_backed")
    fn = sum(1 for r, a in lab if r and a.assessment_status != "evidence_backed")
    tn = sum(1 for r, a in lab if not r and a.assessment_status != "evidence_backed")
    abst = [a for r, a in rows if r is None]
    return {
        "labeled_relevant": tp + fn, "labeled_not_relevant": fp + tn, "labeled_abstain": len(abst),
        "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
        "explicit_not_relevant": sum(1 for r, a in lab if not r and a.assessment_status == "not_relevant"),
        "abstained_when_expected": sum(1 for a in abst if a.assessment_status in ABSTAIN),
        "called_relevant_when_abstain_expected": sum(1 for a in abst if a.assessment_status == "evidence_backed"),
    }


def metrics(run: EvalRun) -> dict:
    out = {}
    for who in ("baseline", "checks_only", "agent"):
        rows = [(r.case["label"].get("relevant"), getattr(r, who)) for r in run.results]
        m = _confusion(rows)
        if who == "agent":
            obs = [o for r in run.results for o in r.agent.observed_events]
            m["observations"] = len(obs)
            m["observations_grounded"] = sum(o.check == "quote_found" for o in obs)
            m["observations_unsupported"] = sum(o.check in ("page_not_read", "quote_not_found") for o in obs)
            m["observations_subject_mismatch"] = sum(o.check == "subject_mismatch" for o in obs)
            m["observations_date_not_in_quote"] = sum(o.check == "date_not_in_quote" for o in obs)
            cov = [r for r in run.results if r.case["label"].get("coverage")]
            m["coverage_labeled"] = len(cov)
            m["coverage_correct"] = sum(r.agent.coverage == r.case["label"]["coverage"] for r in cov)
            act = [r for r in run.results if r.case["label"].get("next_action")]
            m["next_action_labeled"] = len(act)
            m["next_action_correct"] = sum(r.agent.recommended_next_action == r.case["label"]["next_action"] for r in act)
            rel = [r for r in run.results if r.case["label"].get("relevant") and r.agent.assessment_status == "evidence_backed"]
            m["roles_checked"] = len(rel)
            m["roles_match"] = sum({x.role_id for x in r.agent.buying_roles} == set(r.case["label"].get("roles", []))
                                   for r in rel)
            m["roles_include_labeled"] = sum(set(r.case["label"].get("roles", [])) <= {x.role_id for x in r.agent.buying_roles}
                                             for r in rel)
            m["llm_calls"] = sum(r.agent.llm_calls for r in run.results)
            m["llm_cents"] = sum(r.agent.llm_cents for r in run.results)
            m["exa_millidollars"] = sum(r.agent.exa_millidollars for r in run.results)
            m["cases"] = len(run.results)
            m["decision_changed_vs_baseline"] = sum(r.agent.assessment_status != r.baseline.assessment_status
                                                    for r in run.results)
        out[who] = m
    return out


def report_markdown(run: EvalRun) -> str:
    m = metrics(run)
    b, c, a = m["baseline"], m["checks_only"], m["agent"]

    def rate(x, n):
        return f"{x}/{n}" + (f" ({100 * x / n:.0f}%)" if n else "")

    lines = ["# Opportunity evaluation: baseline vs agent", "",
             f"{a['cases']} hand-labeled fictional cases (labels written by the case author, not independent "
             "ground truth). Same inputs for both. Agent answers replayed from a recorded cassette.", "",
             "| | Baseline (signal kind, no AI) | Checks only (naive reader, no AI) | Agent (Claude, bounded research) |",
             "|---|---|---|---|"]
    for k, label in [("true_positive", "Relevant, called relevant"), ("false_negative", "Relevant, not called relevant"),
                     ("false_positive", "Not relevant, called relevant"), ("true_negative", "Not relevant, not called relevant"),
                     ("explicit_not_relevant", "Not relevant, explicitly `not_relevant`"),
                     ("abstained_when_expected", "Abstain expected, abstained"),
                     ("called_relevant_when_abstain_expected", "Abstain expected, called relevant")]:
        n_b = b["labeled_relevant"] if k in ("true_positive", "false_negative") else (
            b["labeled_abstain"] if "abstain" in k else b["labeled_not_relevant"])
        lines.append(f"| {label} | {rate(b[k], n_b)} | {rate(c[k], n_b)} | {rate(a[k], n_b)} |")
    lines += ["", "Agent only:", "",
              f"- Observations grounded (quote found in a page read, about the right company): "
              f"{rate(a['observations_grounded'], a['observations'])}",
              f"- Unsupported (page not read or quote not in page): {rate(a['observations_unsupported'], a['observations'])}",
              f"- Subject mismatch (page about another company): {rate(a['observations_subject_mismatch'], a['observations'])}",
              f"- Stated date not in quote: {rate(a['observations_date_not_in_quote'], a['observations'])}",
              f"- Coverage state correct where labeled: {rate(a['coverage_correct'], a['coverage_labeled'])}",
              f"- Next action correct where labeled: {rate(a['next_action_correct'], a['next_action_labeled'])}",
              f"- Buying roles equal to the labeled set, on correctly found relevant cases: "
              f"{rate(a['roles_match'], a['roles_checked'])}; include every labeled role: "
              f"{rate(a['roles_include_labeled'], a['roles_checked'])}",
              f"- Status different from the baseline: {rate(a['decision_changed_vs_baseline'], a['cases'])}",
              f"- Cost: {a['llm_calls']} LLM calls, {a['llm_cents']}¢ LLM (per-call rounding up), "
              f"${a['exa_millidollars'] / 1000:.3f} fictional Exa; {a['llm_cents'] / max(a['cases'], 1):.1f}¢ per case",
              "", "## Per case", "", "| Case | Label | Baseline | Checks only | Agent | Agent next action | Agent coverage |",
              "|---|---|---|---|---|---|---|"]
    for r in run.results:
        lab = r.case["label"].get("relevant")
        lines.append(f"| {r.case['id']} | {'relevant' if lab else 'not relevant' if lab is False else 'abstain'} "
                     f"| {r.baseline.assessment_status} | {r.checks_only.assessment_status} | {r.agent.assessment_status} "
                     f"| {r.agent.recommended_next_action} | {r.agent.coverage} |")
    if run.misses:
        lines += ["", f"**Replay misses:** {len(run.misses)} request(s) not in the cassette."]
    return "\n".join(lines)

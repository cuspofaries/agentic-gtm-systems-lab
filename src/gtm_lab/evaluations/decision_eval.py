"""Business decisions on labeled cases: which accounts get paid contact enrichment, at what cost.

Runs on the results of `opportunity_eval.run_eval` (same inputs, same recorded agent
answers) and compares three enrichment strategies on the same accounts:

- `all_qualified`: today's default, every ICP-qualified new account is enriched;
- `baseline_gated`: enrich when the signal-kind baseline says evidence-backed;
- `opportunity_gated`: enrich when the action policy proposes it from the agent's checked
  assessment, for the mapped personas only.

Contact enrichment is **simulated** at a fixed price per account (`contact_cents`); LLM
and Exa costs of the agent are **observed** from the recording. Exa calls are the
fictional web's, priced at Exa's published rates.
"""

from __future__ import annotations

import math

from ..context import GTMContext
from ..models import OpportunityAssessment, QualificationDecision
from ..policies.opportunity_action import decide
from .opportunity_eval import EvalRun


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _personas_for(a: OpportunityAssessment, ctx: GTMContext) -> list[str]:
    by = {r.id: r for r in ctx.buying_roles}
    out: list[str] = []
    for role in a.buying_roles:
        for p in (by[role.role_id].personas if role.role_id in by else []):
            if p not in out:
                out.append(p)
    return out


def strategies(run: EvalRun, ctx: GTMContext, contact_cents: int) -> dict:
    rows = {"all_qualified": [], "baseline_gated": [], "opportunity_gated": []}
    for r in run.results:
        icp = r.case.get("icp") or "qualified"
        dec = QualificationDecision(account_id=r.case["id"], decision=icp, explanation="", confidence=0.8)
        all_p = [p.name for p in ctx.personas]
        rows["all_qualified"].append((r, icp == "qualified", all_p, "enrich every qualified account"))
        b = r.baseline
        rows["baseline_gated"].append((r, icp == "qualified" and b.assessment_status == "evidence_backed",
                                       _personas_for(b, ctx), b.assessment_status))
        act, why, personas, _ = decide(dec, r.agent, ctx)
        rows["opportunity_gated"].append((r, act == "propose_enrichment", personas, f"{act}: {why}"))
    out = {}
    for name, items in rows.items():
        enriched = [(r, ps) for r, e, ps, _ in items if e]
        rel = [r for r, _e, _p, _w in items if r.case["label"].get("relevant") is True
               and (r.case.get("icp") or "qualified") == "qualified"]
        hits = [r for r, _ in enriched if r.case["label"].get("relevant") is True]
        wasted = [r for r, _ in enriched if r.case["label"].get("relevant") is not True]
        llm = sum(r.agent.llm_cents for r in run.results) if name == "opportunity_gated" else 0
        exa = sum(r.agent.exa_millidollars for r in run.results) if name == "opportunity_gated" else 0
        lab_p = {p for r in hits for p in r.case["label"].get("personas", [])}
        persona_hits = sum(len(set(ps) & set(r.case["label"].get("personas", []))) for r, ps in enriched
                           if r.case["label"].get("relevant") is True)
        persona_proposed = sum(len(ps) for r, ps in enriched if r.case["label"].get("relevant") is True)
        persona_labeled = sum(len(r.case["label"].get("personas", [])) for r in hits)
        out[name] = {
            "accounts": len(items), "enriched": len(enriched), "calls_avoided": sum(1 for _r, e, _p, _w in items
                                                                                if not e),
            "relevant_enriched": len(hits), "relevant_available": len(rel),
            "relevant_missed": [r.case["id"] for r in rel if r not in hits],
            "enriched_not_relevant": [r.case["id"] for r in wasted],
            "contact_cents_simulated": len(enriched) * contact_cents,
            "llm_cents_observed": llm, "exa_millidollars": exa,
            "total_cents": len(enriched) * contact_cents + llm + math.ceil(exa / 10),
            "persona_precision": (persona_hits, persona_proposed), "persona_recall": (persona_hits, persona_labeled),
            "labeled_personas": sorted(lab_p),
            "queued_for_review_or_research": sum(1 for _r, e, _p, w in items if not e and
                                                 str(w).startswith(("review", "research_more"))),
        }
    return out


def research_quality(run: EvalRun) -> dict:
    s = {"searches": 0, "searches_with_results": 0, "searches_empty": 0, "search_errors": 0, "reads": 0,
         "support_pages_available": 0, "support_pages_read": 0, "contradiction_pages_available": 0,
         "contradiction_pages_read": 0, "noise_pages_read": 0, "disconfirmation_claimed": 0,
         "disconfirmation_credited": 0, "cases_with_contradiction_found": 0}
    for r in run.results:
        web = r.agent_web
        pages = r.case.get("pages") or {}
        steps = [t for t in r.agent.trace if t.get("tool") == "search"]
        s["searches"] += len(steps)
        s["searches_with_results"] += sum(1 for t in steps if t.get("results"))
        s["searches_empty"] += sum(1 for t in steps if t.get("cost_millidollars") is not None and not t.get("results"))
        s["search_errors"] += sum(1 for t in steps if str(t.get("outcome", "")).startswith("HTTP"))
        read = set(web.reads) if web else set()
        s["reads"] += len(read)
        for u, p in pages.items():
            role = p.get("role")
            if role in ("support", "contradiction"):
                s[f"{role}_pages_available"] += 1
                s[f"{role}_pages_read"] += int(u in read)
            elif role == "noise":
                s["noise_pages_read"] += int(u in read)
        s["disconfirmation_claimed"] += len(r.agent.disconfirmation_checks)
        s["disconfirmation_credited"] += sum(c.credited for c in r.agent.disconfirmation_checks)
        s["cases_with_contradiction_found"] += int(any(
            u in read for u, p in pages.items() if p.get("role") == "contradiction"))
    return s


def report(run: EvalRun, ctx: GTMContext, contact_cents: int, opportunity_metrics: dict) -> str:
    st = strategies(run, ctx, contact_cents)
    rq = research_quality(run)
    n = len(run.results)

    def frac(t):
        k, m = t
        return f"{k}/{m}" if m else "n/a"

    lines = ["# Decision evaluation: which accounts get paid contact enrichment", "",
             f"{n} hand-labeled fictional cases (labels by the case author, written before any agent v2 run). "
             f"Contact enrichment simulated at {contact_cents}¢ per enriched account; agent LLM and Exa costs "
             "observed from the recording.", "",
             "| | all_qualified (default) | baseline_gated (signal kind) | opportunity_gated (agent + policy) |",
             "|---|---|---|---|"]
    rows = [("Accounts enriched", "enriched"), ("Contact calls avoided", "calls_avoided"),
            ("Relevant accounts enriched", None), ("Enriched but not relevant / should abstain", None),
            ("Queued for review or research", "queued_for_review_or_research"),
            ("Contact cost, simulated (¢)", "contact_cents_simulated"), ("LLM cost, observed (¢)", "llm_cents_observed"),
            ("Exa cost (¢, rounded up)", None), ("Total (¢)", "total_cents"),
            ("Persona precision on relevant hits", None), ("Persona recall on relevant hits", None)]
    for label, key in rows:
        cells = []
        for name in ("all_qualified", "baseline_gated", "opportunity_gated"):
            v = st[name]
            if key:
                cells.append(str(v[key]))
            elif label.startswith("Relevant"):
                cells.append(f"{v['relevant_enriched']}/{v['relevant_available']}")
            elif label.startswith("Enriched but"):
                cells.append(str(len(v["enriched_not_relevant"])))
            elif label.startswith("Exa"):
                cells.append(str(math.ceil(v["exa_millidollars"] / 10)))
            elif "precision" in label:
                cells.append(frac(v["persona_precision"]))
            else:
                cells.append(frac(v["persona_recall"]))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "Missed relevant accounts (qualified, labeled relevant, not enriched):", ""]
    for name in ("all_qualified", "baseline_gated", "opportunity_gated"):
        lines.append(f"- {name}: {', '.join(st[name]['relevant_missed']) or 'none'}")
    lines += ["", "Enriched although not labeled relevant:", ""]
    for name in ("all_qualified", "baseline_gated", "opportunity_gated"):
        lines.append(f"- {name}: {', '.join(st[name]['enriched_not_relevant']) or 'none'}")
    a = opportunity_metrics["agent"]
    ci = wilson(a["observations_grounded"], a["observations"])
    lines += ["", "## Research quality (agent)", "",
              f"- Searches: {rq['searches']} ({rq['searches_with_results']} with results, {rq['searches_empty']} empty, "
              f"{rq['search_errors']} errors); pages read: {rq['reads']}",
              f"- Support pages read: {rq['support_pages_read']}/{rq['support_pages_available']}; contradiction pages read: "
              f"{rq['contradiction_pages_read']}/{rq['contradiction_pages_available']}; noise pages read: {rq['noise_pages_read']}",
              f"- Disconfirmation checks claimed: {rq['disconfirmation_claimed']}, credited by the code: "
              f"{rq['disconfirmation_credited']}",
              f"- Observations grounded: {a['observations_grounded']}/{a['observations']}"
              + (f" (95% Wilson interval {ci[0]:.0%} to {ci[1]:.0%})" if ci else ""),
              "", "## Per case", "", "| Case | Label | Baseline | Agent | Policy | Semantic checks (verified/rejected) | Disconfirmation (credited/claimed) |",
              "|---|---|---|---|---|---|---|"]
    for r in run.results:
        lab = r.case["label"].get("relevant")
        dec = QualificationDecision(account_id=r.case["id"], decision=r.case.get("icp") or "qualified",
                                    explanation="", confidence=0.8)
        act = decide(dec, r.agent, ctx)[0]
        sm = r.agent.semantic_matches
        dc = r.agent.disconfirmation_checks
        lines.append(f"| {r.case['id']} | {'relevant' if lab else 'not relevant' if lab is False else 'abstain'} | "
                     f"{r.baseline.assessment_status} | {r.agent.assessment_status} | {act} | "
                     f"{sum(m.validation == 'verified' for m in sm)}/{sum(m.validation == 'rejected_by_verifier' for m in sm)} | "
                     f"{sum(c.credited for c in dc)}/{len(dc)} |")
    if run.misses:
        lines += ["", f"**Replay misses:** {len(run.misses)}"]
    return "\n".join(lines)

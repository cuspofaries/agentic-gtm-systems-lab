"""Account Opportunity Brief: the persisted assessments of a run, for a human reviewer.

Observations, hypotheses and next actions are kept apart and labeled: a quote shows what
a page says, a hypothesis is an inference, a role is a guess about who might care.
Nothing here sends anything or writes to a CRM.
"""

from __future__ import annotations

from .models import OpportunityAssessment
from .persistence.store import RunStore


def _actions(run_id: str, store: RunStore) -> dict:
    plan = store.load_plan(run_id)
    return {e.account_id: e for e in plan.entries} if plan else {}


def brief_json(run_id: str, store: RunStore) -> list[dict]:
    accounts = {a.account_id: a for a in store.load_accounts(run_id)}
    entries = _actions(run_id, store)
    rows: dict[str, dict] = {}
    for a in store.load_assessments(run_id):
        acc = accounts.get(a.account_id)
        row = rows.setdefault(a.account_id, {"account_id": a.account_id, "domain": acc.domain if acc else None,
                                             "name": acc.name if acc else None})
        row[a.assessed_by] = a.model_dump(mode="json", exclude={"trace"})
        if a.assessed_by == "agent":
            row["agent"]["steps"] = len([t for t in a.trace if t.get("tool")])
        e = entries.get(a.account_id)
        row["crm"] = ("existing (read-only)" if e is not None and e.crm_action == "skip_existing" and e.crm_account_id
                      else "new")
        if e is not None and e.opportunity_action is not None:
            row["policy"] = e.opportunity_action.model_dump(mode="json")
            row["enrichment_status"] = e.enrichment_status
    return list(rows.values())


def _date(d) -> str:
    return d.date().isoformat() if d else "no date"


def _agent_section(a: OpportunityAssessment) -> list[str]:
    out = [f"- **Status:** `{a.assessment_status}` · **Next action:** `{a.recommended_next_action}` "
           f"· **Coverage:** `{a.coverage}`",
           f"- **Why:** {a.next_action_reason}"]
    if a.agent_view:
        out.append(f"- Agent's own proposal (not used to decide): `{a.agent_view.get('recommended_next_action')}`")
    valid = [o for o in a.observed_events if o.check == "quote_found"]
    dropped = [o for o in a.observed_events if o.check != "quote_found"]
    out.append("\n**Observed evidence** (quotes found in pages read; a quote shows what a page says, not that it is true)")
    if not valid:
        out.append("- none")
    for o in valid:
        when = f"published {_date(o.published_at)}" + (f", event date stated: {o.event_date_stated}"
                                                       if o.event_date_stated else ", no event date stated")
        out.append(f"- `{o.obs_id}` {o.event} · {o.stance} {o.pain_id or ''} · {when}\n"
                   f"  > \"{o.quote}\"\n  {o.url} ({o.page_ref})")
    if dropped:
        out.append(f"- {len(dropped)} observation(s) dropped by the checks: "
                   + ", ".join(f"`{o.obs_id}` {o.check}" for o in dropped))
    out.append("\n**Commercial hypotheses** (inferences)")
    for h in a.pain_hypotheses:
        out.append(f"- {h.pain_id}: {h.statement} → **{h.status}**"
                   + (f" (supporting {', '.join(h.supporting)})" if h.supporting else "")
                   + (f" (contradicting {', '.join(h.contradicting)})" if h.contradicting else ""))
    if a.alternative_explanations or a.unknowns:
        out.append("\n**Counterevidence, alternatives and unknowns** (agent's text)")
        out += [f"- Alternative: {x}" for x in a.alternative_explanations]
        out += [f"- Unknown: {x}" for x in a.unknowns]
    out.append("\n**Buying-group hypotheses** (roles from the GTM Brain; no person identified)")
    out += [f"- {r.role} · {', '.join(r.title_hints)}: {r.reason}" for r in a.buying_roles] or ["- none"]
    steps = [t for t in a.trace if t.get("tool")]
    failed = [f"{t['tool']}: {t.get('outcome')}" for t in steps
              if any(k in str(t.get("outcome")) for k in ("error", "HTTP", "budget", "refused", "no_text"))]
    out.append(f"\n**Research:** {len(steps)} step(s), {a.llm_calls} LLM call(s), {a.llm_cents}¢ LLM, "
               f"${a.exa_millidollars / 1000:.3f} Exa" + (f". Source failures: {'; '.join(failed)}" if failed else ""))
    return out


def render_brief(run_id: str, store: RunStore) -> str:
    rows = brief_json(run_id, store)
    assessments = {(a.account_id, a.assessed_by): a for a in store.load_assessments(run_id)}
    lines = [f"# Account Opportunity Brief: run `{run_id}`\n",
             "Product-specific evidence for accounts new to the CRM, before any contact enrichment. "
             "The ICP decision is shown, never changed. Nothing has been sent or written.\n"]
    if not rows:
        lines.append("No opportunity assessment in this run (run `gtm-lab demo` with `--opportunity exa`).")
        return "\n".join(lines)
    for row in rows:
        agent = assessments.get((row["account_id"], "agent"))
        base = assessments.get((row["account_id"], "baseline"))
        first = agent or base
        lines.append(f"## {row['name'] or row['account_id']} (`{row['domain']}`)")
        lines.append(f"- ICP: **{first.icp_decision}** (by {first.icp_decided_by}) · Offer: `{first.offer_id}` "
                     f"· CRM: {row.get('crm', 'new')}")
        pol = row.get("policy")
        if pol:
            mode = "applied" if pol["applied"] else "shadow estimate, not applied (strategy all_qualified)"
            spend = (f"{pol['provider_calls_avoided']} contact call avoided (up to {pol['cost_avoided_max_cents']}¢)"
                     if pol["provider_calls_avoided"] else f"contact enrichment spent {pol['actual_cost_cents']}¢")
            lines.append(f"- Policy ({mode}): **{pol['action']}** · {pol['reason']} · {spend}"
                         + (f" · personas: {', '.join(pol['personas'])}" if pol["personas"] else ""))
        if base:
            lines.append(f"- Baseline (signal kind only, no AI): `{base.assessment_status}` → "
                         f"`{base.recommended_next_action}`")
        if agent:
            lines += _agent_section(agent)
        else:
            lines.append("- Not investigated (per-run account cap reached, or not selected).")
        lines.append("")
    return "\n".join(lines)

"""What actually ran in a plan, per component: configured vs executed, calls, cost, failures.

Built once at the end of `AccountDiscoveryWorkflow.plan()` from the persisted outcome of
the run (accounts, decisions, assessments, plan entries) and the components' own
counters, then written to the event log as `execution.summary`, so `gtm-lab demo --json`
and `gtm-lab inspect` read it back after a restart.

Units are part of the key names: `*_cents` (US cents), `*_millidollars` (thousandths of a
US dollar, Exa). They are never added together.

`kind` says how a component's calls were served: `simulated` (fixtures, mock CRM,
scripted stand-ins), `live` (a real API), `replayed` (answers recorded earlier), or
`recorded` (a live call whose answer is being recorded).
"""

from __future__ import annotations

from typing import Any

SIMULATED_CLASSES = {"FixtureDiscoveryProvider", "FixtureEnrichmentProvider", "MockCRM"}


def kind_of(component: Any) -> str | None:
    if component is None:
        return None
    explicit = getattr(component, "execution_kind", None)
    if explicit:
        return explicit
    return "simulated" if type(component).__name__ in SIMULATED_CLASSES else "live"


def _name(component: Any) -> str | None:
    return None if component is None else (getattr(component, "name", None) or type(component).__name__)


def snapshot(component: Any) -> dict:
    """LLM and Exa counters of an agentic component, to diff before and after a plan."""
    if component is None:
        return {}
    budget = getattr(component, "budget", None)
    if budget is not None:  # research loop and opportunity investigator share ResearchBudget
        return {"llm_calls": budget.llm_calls, "llm_cents": budget.llm_spent_cents,
                "exa_millidollars": budget.exa_spent_millidollars, "errors": len(budget.errors)}
    provider = getattr(component, "provider_call", None)  # LLMQualifier -> AnthropicProviderCall
    if provider is not None and hasattr(provider, "calls"):
        return {"llm_calls": provider.calls, "llm_cents": provider.spent_cents, "exa_millidollars": 0,
                "errors": len(getattr(provider, "errors", []))}
    return {"llm_calls": 0, "llm_cents": 0, "exa_millidollars": 0, "errors": 0}


def _delta(before: dict, after: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}


def build(*, workflow, plan, accounts, decisions, assessments, fallback_before: dict,
          opportunity_before: dict) -> dict:
    wf = workflow
    fb = wf.fallback_qualifier
    fb_kind = type(fb).__name__ if fb is not None else None
    fb_used = [d for d in decisions if d.decided_by in ("llm", "hybrid")]
    fb_delta = _delta(fallback_before, snapshot(fb)) if fb is not None else {}
    model = getattr(fb, "model", None) or getattr(getattr(fb, "provider_call", None), "model", None)

    def llm_block(configured, used_accounts, delta, component, model_name, extra=None):
        return {
            "configured": configured, "executed": bool(delta.get("llm_calls") or used_accounts),
            "accounts": used_accounts, "llm_calls": delta.get("llm_calls", 0),
            "llm_cost_cents": delta.get("llm_cents", 0), "exa_cost_millidollars": delta.get("exa_millidollars", 0),
            "errors": delta.get("errors", 0), "provider": "anthropic" if configured else None,
            "model": model_name if configured else None, "kind": kind_of(component) if configured else None,
            **(extra or {}),
        }

    out: dict[str, Any] = {}
    out["icp_rules"] = {"configured": True, "executed": bool(decisions), "accounts": len(decisions),
                        "kind": "deterministic"}
    out["llm_qualifier"] = llm_block(fb_kind == "LLMQualifier", len(fb_used) if fb_kind == "LLMQualifier" else 0,
                                     fb_delta if fb_kind == "LLMQualifier" else {}, fb, model)
    out["company_research"] = llm_block(fb_kind == "ResearchQualifier",
                                        len(fb_used) if fb_kind == "ResearchQualifier" else 0,
                                        fb_delta if fb_kind == "ResearchQualifier" else {}, fb, model)
    opp = wf.opportunity
    agent_rows = [a for a in assessments if a.assessed_by == "agent"]
    opp_delta = _delta(opportunity_before, snapshot(opp)) if opp is not None else {}
    out["opportunity_intelligence"] = llm_block(
        opp is not None, len(agent_rows), opp_delta, opp, getattr(opp, "model", None),
        {"baseline_assessments": sum(1 for a in assessments if a.assessed_by == "baseline"),
         "incomplete_or_failed": sum(1 for a in agent_rows if a.coverage in ("incomplete", "failed")),
         "scope": getattr(wf, "opportunity_scope", "new")})
    searched = [a for a in accounts if a.signal_search in ("found", "no_result", "failed")]
    out["signal_discovery"] = {
        "configured": wf.signals is not None, "executed": bool(wf.signals is not None and searched),
        "provider": _name(wf.signals), "kind": kind_of(wf.signals), "calls": len(searched) if wf.signals else 0,
        "cost_millidollars": plan.signals_spent_millidollars,
        "failed": sum(1 for a in accounts if a.signal_search == "failed") if wf.signals else 0,
        "skipped_budget_exhausted": sum(1 for a in accounts if a.signal_search == "budget_exhausted"),
    }
    entries = plan.entries
    called = [e for e in entries if e.enrichment_status in ("done", "provider_failed")]
    out["contact_enrichment"] = {
        "configured": True, "executed": bool(called), "provider": _name(wf.enrichment),
        "kind": kind_of(wf.enrichment), "strategy": getattr(wf, "enrichment_strategy", "all_qualified"),
        "calls": len(called), "cost_cents": plan.enrichment_spent_cents,
        "budget_cents": plan.enrichment_budget_cents,
        "skipped_budget_exceeded": sum(1 for e in entries if e.enrichment_status == "budget_exceeded"),
        "deferred_by_policy": sum(1 for e in entries if e.enrichment_status == "deferred_by_policy"),
        "provider_failed": sum(1 for e in entries if e.enrichment_status == "provider_failed"),
        "overcharges": sum(1 for e in entries for f in e.policy_failures if f.startswith("Provider overcharge")),
    }
    out["crm"] = {"configured": True, "executed": True, "adapter": _name(wf.crm), "kind": kind_of(wf.crm),
                  "identity": plan.crm_identity, "reconciled_accounts": sum(1 for d in decisions
                                                                             if d.decision == "qualified"),
                  "writes_during_plan": 0}
    live_paid = [k for k, v in out.items()
                 if v.get("kind") in ("live", "recorded") and v.get("executed")
                 and (v.get("llm_calls") or v.get("calls") or k == "crm")]
    llm_calls = sum(out[k].get("llm_calls", 0) for k in ("llm_qualifier", "company_research", "opportunity_intelligence"))
    return {
        "components": out,
        "llm_calls_total": llm_calls,
        "llm_cost_cents_total": sum(out[k].get("llm_cost_cents", 0)
                                    for k in ("llm_qualifier", "company_research", "opportunity_intelligence")),
        "exa_cost_millidollars_total": (out["signal_discovery"]["cost_millidollars"]
                                        + out["company_research"]["exa_cost_millidollars"]
                                        + out["opportunity_intelligence"]["exa_cost_millidollars"]),
        "contact_cost_cents_total": out["contact_enrichment"]["cost_cents"],
        "live_components": live_paid,
        "offline": not live_paid,
    }

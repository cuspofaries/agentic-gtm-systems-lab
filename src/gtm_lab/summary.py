"""Structured run summaries built only from persisted state.

Used by `gtm-lab demo --json`, `gtm-lab inspect` and `gtm-lab runs --json`, so that
callers (scripts, Claude Code, other tools) read data instead of parsing terminal tables.
Nothing here recomputes a decision: it reads what the run stored.
"""

from __future__ import annotations

import json
from pathlib import Path

from .observability.events import EventRecorder
from .persistence.store import RunStore

NEXT_STEPS = {
    "proposed": "Review the artifact, then `gtm-lab approve <run-id>` or `gtm-lab reject <run-id>`.",
    "approved": "Approved, nothing written yet. `gtm-lab sync <run-id>` writes to the local mock CRM.",
    "synced": "Done. Re-running `gtm-lab sync <run-id>` is a no-op.",
    "partial": "Some writes failed or went stale. See `write_results`; re-run `gtm-lab sync <run-id>` for failed writes, re-plan stale ones.",
    "failed": "Every write failed. See `write_results`, fix the cause, then re-run `gtm-lab sync <run-id>`.",
    "rejected": "Rejected by a human. Nothing will be written for this run.",
    "draft": "Plan was not completed.",
}


def state_paths(state_dir: Path, run_id: str) -> dict[str, str]:
    return {
        "state_dir": str(state_dir),
        "runs_db": str(state_dir / "runs.sqlite"),
        "crm_db": str(state_dir / "crm.sqlite"),
        "events_log": str(state_dir / "events.jsonl"),
        "review_artifact": str(state_dir / f"review_{run_id}.md"),
    }


def run_summary(run_id: str, state_dir: Path) -> dict | None:
    store = RunStore(state_dir / "runs.sqlite")
    plan = store.load_plan(run_id)
    if plan is None:
        return None
    accounts = {a.account_id: a for a in store.load_accounts(run_id)}
    decisions = store.load_decisions(run_id)
    writes = store.load_write_results(run_id)
    events = EventRecorder(state_dir / "events.jsonl").read(run_id)

    start = next((e["payload"] for e in events if e["event_type"] == "workflow.start"), {})
    discovery = next((e["payload"] for e in events if e["event_type"] == "discovery.complete"), {})
    fallbacks = [e["payload"] for e in events if e["event_type"] == "qualification.fallback"]
    paths = state_paths(state_dir, run_id)

    in_plan = {e.account_id for e in plan.entries}
    status = plan.status.value
    return {
        "run_id": run_id,
        "mode": "agentic" if start.get("llm_fallback_configured") else "offline",
        "llm_calls_made": len(fallbacks),
        "plan_status": status,
        "next_step": NEXT_STEPS.get(status, ""),
        "crm_target": "local mock CRM (SQLite)",
        "paths": paths,
        "review_artifact_exists": Path(paths["review_artifact"]).exists(),
        "discovery": {
            "accounts_returned": len(accounts),
            "source_failures": discovery.get("source_failures", []),
        },
        "qualification": {
            k: sum(1 for d in decisions if d.decision == k)
            for k in ("qualified", "not_qualified", "insufficient_evidence")
        },
        "enrichment": {
            "budget_cents": plan.enrichment_budget_cents,
            "spent_cents": plan.enrichment_spent_cents,
        },
        "entries": [
            {
                "account_id": e.account_id,
                "name": accounts[e.account_id].name if e.account_id in accounts else None,
                "domain": accounts[e.account_id].domain if e.account_id in accounts else None,
                "action": "skipped" if e.skipped else e.crm_action,
                "skip_reason": e.skip_reason,
                "enrichment_status": e.enrichment_status,
                "contacts_proposed": len(e.contacts_to_create),
                "policy_failures": e.policy_failures,
                "approvable": not e.skipped and not e.policy_failures,
            }
            for e in plan.entries
        ],
        "not_in_plan": [
            {"account_id": d.account_id, "decision": d.decision, "decided_by": d.decided_by}
            for d in decisions
            if d.account_id not in in_plan
        ],
        "writes": {
            k: sum(1 for w in writes if w.outcome == k)
            for k in ("ok", "idempotent_noop", "failed", "stale_plan")
        },
    }


def run_detail(run_id: str, state_dir: Path) -> dict | None:
    """Everything persisted for a run: the summary plus raw records and audit events."""
    summary = run_summary(run_id, state_dir)
    if summary is None:
        return None
    store = RunStore(state_dir / "runs.sqlite")
    raw = store.inspect(run_id)  # keeps the historical keys: accounts, decisions, plan, write_results
    raw["matches"] = [json.loads(m.model_dump_json()) for m in store.load_matches(run_id)]
    raw["costs"] = [json.loads(c.model_dump_json()) for c in store.load_costs(run_id)]
    raw["events"] = EventRecorder(state_dir / "events.jsonl").read(run_id)
    raw["summary"] = summary
    return raw

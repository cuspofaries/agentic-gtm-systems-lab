"""Automated evaluation of a completed run.

These checks sit between "unit tests" and "accuracy metrics":
- They don't require labeled ground truth (which would be fabricated here).
- They enforce the integrity rules the brief insists on.

If `evaluate_run` returns any `failures`, the run is NOT release-quality - regardless of
how many entries made it through the policy layer. Add new checks here as the workflow
grows new ways to be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..persistence.store import RunStore


@dataclass
class EvalReport:
    run_id: str
    checks_passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, float | int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


def evaluate_run(run_id: str, store: RunStore) -> EvalReport:
    report = EvalReport(run_id=run_id)
    plan = store.load_plan(run_id)
    if plan is None:
        report.failures.append(f"No plan for run {run_id}")
        return report

    accounts = store.load_accounts(run_id)
    decisions = store.load_decisions(run_id)
    writes = store.load_write_results(run_id)

    # 1. Every account must have a decision
    acc_ids = {a.account_id for a in accounts}
    dec_ids = {d.account_id for d in decisions}
    missing = acc_ids - dec_ids
    if missing:
        report.failures.append(f"Accounts without a qualification decision: {sorted(missing)[:5]}")
    else:
        report.checks_passed.append("every_account_has_decision")

    # 2. Budget never exceeded
    if plan.enrichment_spent_cents > plan.enrichment_budget_cents:
        report.failures.append(
            f"Budget exceeded: spent {plan.enrichment_spent_cents}¢ > "
            f"cap {plan.enrichment_budget_cents}¢"
        )
    else:
        report.checks_passed.append("budget_respected")

    # 3. No synced entry has policy failures
    sync_ok_ids = {w.account_id for w in writes if w.outcome == "ok" and w.operation == "create_account"}
    policy_failed_ids = {e.account_id for e in plan.entries if e.policy_failures}
    overlap = sync_ok_ids & policy_failed_ids
    if overlap:
        report.failures.append(
            f"Entries with policy failures reached CRM create: {sorted(overlap)[:5]}"
        )
    else:
        report.checks_passed.append("no_policy_failed_entry_synced")

    # 4. No duplicate create_account for the same account_id
    seen = {}
    for w in writes:
        if w.operation == "create_account" and w.outcome == "ok":
            seen[w.account_id] = seen.get(w.account_id, 0) + 1
    duplicates = [k for k, v in seen.items() if v > 1]
    if duplicates:
        report.failures.append(f"Duplicate create_account writes: {duplicates[:5]}")
    else:
        report.checks_passed.append("no_duplicate_account_writes")

    # 5. Signals used as evidence must still be within freshness window per the ICP context
    # Encoded indirectly: policy_failures catches this during plan time. Here we just report.
    report.metrics.update(
        {
            "accounts_discovered": len(accounts),
            "qualified": sum(1 for d in decisions if d.decision == "qualified"),
            "not_qualified": sum(1 for d in decisions if d.decision == "not_qualified"),
            "insufficient_evidence": sum(
                1 for d in decisions if d.decision == "insufficient_evidence"
            ),
            "entries_proposed": len(plan.entries),
            "entries_approvable": len(plan.approvable_entries),
            "entries_skipped_existing": sum(
                1 for e in plan.entries if e.skipped and e.skip_reason == "Already exists in CRM"
            ),
            "entries_skipped_ambiguous": sum(
                1 for e in plan.entries if e.skipped and "Ambiguous" in (e.skip_reason or "")
            ),
            "write_ok": sum(1 for w in writes if w.outcome == "ok"),
            "write_failed": sum(1 for w in writes if w.outcome == "failed"),
            "write_noop": sum(1 for w in writes if w.outcome == "idempotent_noop"),
            "write_stale_plan": sum(1 for w in writes if w.outcome == "stale_plan"),
        }
    )
    return report

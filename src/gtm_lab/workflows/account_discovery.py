"""The Account-Discovery → CRM Sync workflow.

Phases:

1. Load context.
2. Discover accounts via a `DiscoveryProvider`.
3. Qualify each account via a `Qualifier`.
4. Reconcile each qualified account against the CRM.
5. For accounts not already in the CRM, propose enrichment (budget-enforced).
6. Evaluate the resulting WritePlan against policies.
7. Return the WritePlan in state=PROPOSED - awaiting human approval.
8. On approval, apply the plan idempotently and record outcomes.

The orchestrator persists its state via the `RunStore` after every phase so an
interrupted run can resume from where it stopped.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..agents.qualifier import DeterministicQualifier, Qualifier
from ..context import GTMContext
from ..crm.base import CRMAdapter, CRMError
from ..models import (
    Account,
    ContactProposal,
    EnrichmentCost,
    QualificationDecision,
    ReconciliationMatch,
    WriteOperationResult,
    WritePlan,
    WritePlanEntry,
    WritePlanStatus,
)
from ..observability.events import EventRecorder
from ..persistence.store import RunStore
from ..policies.rules import evaluate_entry
from ..providers.base import DiscoveryProvider, EnrichmentProvider
from ..providers.signals import SignalProvider
from ..util import canonicalize_domain, ts_slug

# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class Reconciliation:
    """Pure function over a list of accounts + the CRM state.

    Produces one `ReconciliationMatch` per account. Matching is domain-based (canonicalized).
    Ambiguity is explicit: if two CRM accounts share a domain, the match_type is "ambiguous"
    and no automatic merge is proposed - human review decides.
    """

    def reconcile(
        self, accounts: Iterable[Account], crm: CRMAdapter
    ) -> list[ReconciliationMatch]:
        matches: list[ReconciliationMatch] = []
        for acc in accounts:
            domain = canonicalize_domain(acc.domain)
            crm_hits = crm.find_accounts_by_domain(domain)
            if not crm_hits:
                matches.append(
                    ReconciliationMatch(account_id=acc.account_id, match_type="none")
                )
                continue
            if len(crm_hits) == 1:
                matches.append(
                    ReconciliationMatch(
                        account_id=acc.account_id,
                        match_type="exact_domain",
                        crm_account_id=crm_hits[0].crm_account_id,
                    )
                )
                continue
            # More than one hit on the same domain: ambiguous
            matches.append(
                ReconciliationMatch(
                    account_id=acc.account_id,
                    match_type="ambiguous",
                    crm_account_id=None,
                    duplicates_in_crm=[h.crm_account_id for h in crm_hits],
                )
            )
        return matches


# ---------------------------------------------------------------------------
# Enrichment budget
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentBudget:
    """Deterministic budget enforcer. Not an LLM, not a prompt - a cents counter."""

    cap_cents: int
    spent_cents: int = 0

    def can_spend(self, cost_cents: int) -> bool:
        return self.spent_cents + cost_cents <= self.cap_cents

    def charge(self, cost_cents: int) -> None:
        if not self.can_spend(cost_cents):
            raise ValueError("EnrichmentBudget.charge called over cap - check can_spend first")
        self.spent_cents += cost_cents

    @property
    def remaining(self) -> int:
        return max(0, self.cap_cents - self.spent_cents)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class AccountDiscoveryWorkflow:
    def __init__(
        self,
        *,
        context: GTMContext,
        discovery: DiscoveryProvider,
        enrichment: EnrichmentProvider,
        crm: CRMAdapter,
        store: RunStore,
        recorder: EventRecorder,
        qualifier: Qualifier | None = None,
        fallback_qualifier: Qualifier | None = None,
        signals: SignalProvider | None = None,
    ):
        self.context = context
        self.signals = signals
        self.discovery = discovery
        self.enrichment = enrichment
        self.crm = crm
        self.store = store
        self.recorder = recorder
        self.qualifier = qualifier or DeterministicQualifier()
        # Consulted ONLY when the primary qualifier returns insufficient_evidence.
        self.fallback_qualifier = fallback_qualifier
        self.reconciler = Reconciliation()

    # --- 1→6: build the plan ---

    def _find_signals(self, run_id: str, accounts: list[Account]) -> int:
        """Attach dated web signals to each account, within the signal budget.

        Returns what was spent, in thousandths of a dollar. The cap is checked before
        each call against the most a call can cost, then charged what the provider reports.
        """
        if self.signals is None:
            return 0
        cap = self.context.signals.budget_millidollars
        spent = 0
        for acc in accounts:
            if spent + self.signals.max_cost_millidollars_per_call > cap:
                self.recorder.record(run_id, "signals.budget_exceeded",
                                     {"account_id": acc.account_id, "spent_millidollars": spent,
                                      "cap_millidollars": cap})
                continue
            r = self.signals.find(acc, max_age_days=self.context.signal_policy.max_signal_age_days)
            spent += r.cost_millidollars
            acc.signals.extend(r.signals)
            self.recorder.record(run_id, "signals.account", {
                "account_id": acc.account_id,
                "provider": self.signals.name,
                "kept": [{"kind": s.kind, "date": s.event_date.isoformat() if s.event_date else None,
                          "url": s.source.url} for s in r.signals],
                "rejected": dict(r.rejected),
                "cost_millidollars": r.cost_millidollars,
                "failures": r.failures,
            })
        return spent

    def plan(self, *, limit: int = 50, run_id: str | None = None) -> WritePlan:
        run_id = run_id or f"run_{ts_slug()}"
        self.recorder.record(
            run_id,
            "workflow.start",
            {"limit": limit, "llm_fallback_configured": self.fallback_qualifier is not None},
        )

        # 2. Discovery
        disc = self.discovery.discover(limit=limit)
        self.recorder.record(
            run_id,
            "discovery.complete",
            {
                "returned": len(disc.accounts),
                "source_failures": disc.source_failures,
                "coverage": disc.search_coverage,
            },
        )
        signals_spent = self._find_signals(run_id, disc.accounts)
        self.store.save_accounts(run_id, disc.accounts)

        # 3. Qualification
        qualified: list[Account] = []
        decisions: list[QualificationDecision] = []
        for acc in disc.accounts:
            decision = self.qualifier.qualify(acc, self.context)
            baseline = decision
            if decision.decision == "insufficient_evidence" and self.fallback_qualifier:
                decision = self.fallback_qualifier.qualify(acc, self.context)
                self.recorder.record(
                    run_id,
                    "qualification.fallback",
                    {
                        "account_id": acc.account_id,
                        "baseline_decision": baseline.decision,
                        "fallback_decision": decision.decision,
                        "fallback_decided_by": decision.decided_by,
                        "agrees_with_baseline": decision.decision == baseline.decision,
                    }
                    # The research loop's steps (tool, input, outcome, cost), when it ran.
                    | ({"steps": list(self.fallback_qualifier.last_trace)}
                       if getattr(self.fallback_qualifier, "last_trace", None) else {}),
                )
            decisions.append(decision)
            self.recorder.record(
                run_id,
                "qualification.decision",
                {
                    "account_id": acc.account_id,
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "decided_by": decision.decided_by,
                },
            )
            if decision.decision == "qualified":
                qualified.append(acc)
        self.store.save_decisions(run_id, decisions)

        # 4. Reconciliation (in-batch dedup first, then CRM-side)
        matches = self.reconciler.reconcile(qualified, self.crm)
        matches_by_id = {m.account_id: m for m in matches}
        # In-batch dedup: if two qualified accounts share a canonical domain, the
        # later one is marked ambiguous - it needs human review, not a blind write.
        seen_domains: dict[str, str] = {}
        for acc in qualified:
            if acc.domain in seen_domains:
                matches_by_id[acc.account_id] = ReconciliationMatch(
                    account_id=acc.account_id,
                    match_type="ambiguous",
                    duplicates_in_crm=[seen_domains[acc.domain]],
                )
            else:
                seen_domains[acc.domain] = acc.account_id
        self.recorder.record(
            run_id,
            "reconciliation.complete",
            {
                "qualified": len(qualified),
                "already_in_crm": sum(
                    1 for m in matches_by_id.values() if m.match_type == "exact_domain"
                ),
                "ambiguous": sum(
                    1 for m in matches_by_id.values() if m.match_type == "ambiguous"
                ),
            },
        )
        self.store.save_matches(run_id, list(matches_by_id.values()))

        # 5. Enrichment + 6. Policy evaluation → WritePlan entries
        budget = EnrichmentBudget(cap_cents=self.context.policy.enrichment_budget_cents)
        entries: list[WritePlanEntry] = []
        costs: list[EnrichmentCost] = []

        for acc in qualified:
            match = matches_by_id[acc.account_id]
            if match.match_type == "ambiguous":
                entries.append(
                    WritePlanEntry(
                        account_id=acc.account_id,
                        crm_action="skip_existing",
                        crm_account_id=None,
                        skipped=True,
                        skip_reason=(
                            f"Ambiguous CRM match (candidates: {match.duplicates_in_crm})"
                        ),
                    )
                )
                continue
            if match.match_type == "exact_domain":
                entries.append(
                    WritePlanEntry(
                        account_id=acc.account_id,
                        crm_action="skip_existing",
                        crm_account_id=match.crm_account_id,
                        skipped=True,
                        skip_reason="Already exists in CRM",
                    )
                )
                continue

            # Fresh account → enrich only if budget allows
            persona_names = [p.name for p in self.context.personas]
            max_cost = self.enrichment.max_cost_cents_per_call
            new_contacts: list[ContactProposal] = []
            enrichment_status = "budget_exceeded"
            if budget.can_spend(max_cost):
                enrich_result = self.enrichment.enrich(acc, persona_names)
                reported = enrich_result.cost_cents
                # A provider cannot charge more than the maximum it announced.
                cost = max_cost if reported is None else min(reported, max_cost)
                budget.charge(cost)
                costs.append(
                    EnrichmentCost(
                        account_id=acc.account_id,
                        persona=",".join(persona_names),
                        cost_cents=cost,
                        provider=self.enrichment.name,
                    )
                )
                new_contacts = enrich_result.contacts
                enrichment_status = "provider_failed" if enrich_result.source_failures else "done"
                self.recorder.record(
                    run_id,
                    "enrichment.spent",
                    {
                        "account_id": acc.account_id,
                        "cost_cents": cost,
                        "returned_contacts": len(new_contacts),
                        "source_failures": enrich_result.source_failures,
                    },
                )
            else:
                self.recorder.record(
                    run_id,
                    "enrichment.budget_exceeded",
                    {"account_id": acc.account_id, "remaining_cents": budget.remaining},
                )

            entry = WritePlanEntry(
                account_id=acc.account_id,
                crm_action="create_account",
                crm_account_id=None,
                contacts_to_create=new_contacts,
                enrichment_status=enrichment_status,
            )
            # Policy evaluation
            account_obj = _by_id(disc.accounts, acc.account_id)
            decision = next(d for d in decisions if d.account_id == acc.account_id)
            entry.policy_failures = evaluate_entry(
                account=account_obj,
                decision=decision,
                entry=entry,
                context=self.context,
            )
            entries.append(entry)

        self.store.save_costs(run_id, costs)

        plan = WritePlan(
            run_id=run_id,
            status=WritePlanStatus.PROPOSED,
            entries=entries,
            enrichment_budget_cents=budget.cap_cents,
            enrichment_spent_cents=budget.spent_cents,
            crm_target=self.crm.name,
            crm_identity=self.crm.identity(),
            signals_budget_millidollars=(self.context.signals.budget_millidollars
                                         if self.signals is not None else None),
            signals_spent_millidollars=signals_spent,
        )
        self.store.save_plan(plan)
        self.recorder.record(
            run_id,
            "plan.proposed",
            {
                "total_entries": len(entries),
                "approvable": len(plan.approvable_entries),
                "spent_cents": plan.enrichment_spent_cents,
            },
        )
        return plan

    # --- 7: human approval ---

    def approve(self, run_id: str) -> WritePlan:
        plan = self.store.load_plan(run_id)
        if plan is None:
            raise FileNotFoundError(f"No plan found for run_id={run_id}")
        if plan.status != WritePlanStatus.PROPOSED:
            raise ValueError(f"Cannot approve plan in status {plan.status}")
        plan.status = WritePlanStatus.APPROVED
        self.store.save_plan(plan)
        self.recorder.record(run_id, "plan.approved", {"entries": len(plan.approvable_entries)})
        return plan

    def reject(self, run_id: str, reason: str = "") -> WritePlan:
        plan = self.store.load_plan(run_id)
        if plan is None:
            raise FileNotFoundError(f"No plan found for run_id={run_id}")
        if plan.status != WritePlanStatus.PROPOSED:
            raise ValueError(f"Cannot reject plan in status {plan.status}")
        plan.status = WritePlanStatus.REJECTED
        self.store.save_plan(plan)
        self.recorder.record(run_id, "plan.rejected", {"reason": reason[:500]})
        return plan

    # --- 8: idempotent sync ---

    def sync(self, run_id: str, *, max_retries: int = 2) -> list[WriteOperationResult]:
        """Apply an approved plan. Safe to call repeatedly.

        Each entry is revalidated against current CRM state before writing:
        - already written by this run (same external_ref) -> idempotent_noop
        - domain now owned by another writer -> stale_plan, nothing written
        - otherwise -> create, retrying retriable errors
        """
        plan = self.store.load_plan(run_id)
        if plan is None:
            raise FileNotFoundError(f"No plan found for run_id={run_id}")
        if plan.status not in {
            WritePlanStatus.APPROVED,
            WritePlanStatus.PARTIAL,
            WritePlanStatus.SYNCED,
            WritePlanStatus.FAILED,
        }:
            raise ValueError(f"Cannot sync plan in status {plan.status}")
        if plan.crm_target != self.crm.name:
            raise ValueError(
                f"Plan {run_id} was reconciled against the '{plan.crm_target}' CRM; "
                f"refusing to sync it to '{self.crm.name}'. Re-plan against '{self.crm.name}'."
            )
        current = self.crm.identity()
        if plan.crm_identity is None and self.crm.name != "mock":
            raise ValueError(
                f"Plan {run_id} does not record which {self.crm.name} instance it was made "
                f"against; refusing to write to {current}. Re-plan."
            )
        if plan.crm_identity is not None and plan.crm_identity != current:
            raise ValueError(
                f"Plan {run_id} was made against {plan.crm_identity} but the credentials now "
                f"point to {current}; refusing to write. Re-plan against {current}."
            )
        self.crm.check_ready()

        accounts = {a.account_id: a for a in self.store.load_accounts(run_id)}
        results: list[WriteOperationResult] = []

        for entry in plan.entries:
            if entry.skipped or entry.policy_failures:
                results.append(
                    WriteOperationResult(
                        account_id=entry.account_id,
                        operation="skip",
                        outcome="idempotent_noop",
                        error=entry.skip_reason or ";".join(entry.policy_failures) or None,
                    )
                )
                continue

            acc_result, crm_acc_id = self._sync_account(
                accounts[entry.account_id], run_id, max_retries=max_retries
            )
            results.append(acc_result)
            if crm_acc_id is None:
                continue
            for contact in entry.contacts_to_create:
                results.append(
                    self._sync_contact(
                        contact, crm_account_id=crm_acc_id, run_id=run_id, max_retries=max_retries
                    )
                )

        applied = [r for r in results if r.operation != "skip"]
        bad = [r for r in applied if r.outcome in {"failed", "stale_plan"}]
        if not bad:
            plan.status = WritePlanStatus.SYNCED
        elif len(bad) == len(applied):
            plan.status = WritePlanStatus.FAILED
        else:
            plan.status = WritePlanStatus.PARTIAL

        self.store.save_plan(plan)
        self.store.save_write_results(run_id, results)
        self.recorder.record(
            run_id,
            "sync.complete",
            {
                "status": plan.status.value,
                "ok": sum(1 for r in results if r.outcome == "ok"),
                "noop": sum(1 for r in results if r.outcome == "idempotent_noop"),
                "failed": sum(1 for r in results if r.outcome == "failed"),
                "stale_plan": sum(1 for r in results if r.outcome == "stale_plan"),
            },
        )
        return results

    def _sync_account(
        self, acc: Account, run_id: str, *, max_retries: int
    ) -> tuple[WriteOperationResult, str | None]:
        external_ref = f"gtm-lab:{run_id}:{acc.account_id}"

        try:
            mine = self.crm.find_account_by_external_ref(external_ref)
            others = [] if mine else self.crm.find_accounts_by_domain(acc.domain)
        except CRMError as e:
            return (
                WriteOperationResult(
                    account_id=acc.account_id,
                    operation="create_account",
                    outcome="failed",
                    error=f"Could not revalidate against the CRM: {e}",
                ),
                None,
            )
        if mine is not None:
            return (
                WriteOperationResult(
                    account_id=acc.account_id, operation="create_account", outcome="idempotent_noop"
                ),
                mine.crm_account_id,
            )

        if others:
            return (
                WriteOperationResult(
                    account_id=acc.account_id,
                    operation="create_account",
                    outcome="stale_plan",
                    error=(
                        f"{acc.domain} was created in the CRM after approval "
                        f"({', '.join(o.crm_account_id for o in others)}); re-plan this account"
                    ),
                ),
                None,
            )

        attempt = 0
        last_error: str | None = None
        while attempt <= max_retries:
            attempt += 1
            try:
                created = self.crm.create_account(
                    domain=acc.domain, name=acc.name, source=run_id, external_ref=external_ref
                )
                return (
                    WriteOperationResult(
                        account_id=acc.account_id,
                        operation="create_account",
                        outcome="ok",
                        attempt=attempt,
                    ),
                    created.crm_account_id,
                )
            except CRMError as e:
                last_error = str(e)
                if not e.retriable:
                    break
        return (
            WriteOperationResult(
                account_id=acc.account_id,
                operation="create_account",
                outcome="failed",
                error=last_error,
                attempt=attempt,
            ),
            None,
        )

    def _sync_contact(
        self, contact: ContactProposal, *, crm_account_id: str, run_id: str, max_retries: int
    ) -> WriteOperationResult:
        external_ref = (
            f"gtm-lab:{run_id}:contact:{contact.account_id}:{contact.persona}:"
            f"{contact.first_name}:{contact.last_name}"
        )
        try:
            already = self.crm.find_contact_by_external_ref(external_ref)
        except CRMError as e:
            return WriteOperationResult(
                account_id=contact.account_id,
                operation="create_contact",
                outcome="failed",
                error=f"Could not revalidate against the CRM: {e}",
            )
        if already is not None:
            return WriteOperationResult(
                account_id=contact.account_id, operation="create_contact", outcome="idempotent_noop"
            )
        attempt = 0
        last_error: str | None = None
        while attempt <= max_retries:
            attempt += 1
            try:
                self.crm.create_contact(
                    contact, crm_account_id=crm_account_id, external_ref=external_ref
                )
                return WriteOperationResult(
                    account_id=contact.account_id,
                    operation="create_contact",
                    outcome="ok",
                    attempt=attempt,
                )
            except CRMError as e:
                last_error = str(e)
                if not e.retriable:
                    break
        return WriteOperationResult(
            account_id=contact.account_id,
            operation="create_contact",
            outcome="failed",
            error=last_error,
            attempt=attempt,
        )


def _by_id(accounts: list[Account], account_id: str) -> Account:
    for a in accounts:
        if a.account_id == account_id:
            return a
    raise KeyError(f"Account {account_id} not found in run")

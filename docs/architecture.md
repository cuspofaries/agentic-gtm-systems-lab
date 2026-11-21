# Architecture

## Design principles

1. **Separation of concerns.** Agent reasoning is strictly isolated from deterministic business operations. The qualifier decides; the orchestrator applies rules; the CRM adapter writes. No single module tries to do all three.
2. **Provider independence.** No hard dependency on Clay, Apollo, HubSpot, Salesforce, or any single vendor. Every external capability is behind an abstract class.
3. **Explicit data contracts.** Every interface is a Pydantic model. Inputs and outputs are validated on the way in and on the way out.
4. **Persistent state.** Workflow execution survives restarts. The SQLite store rebuilds every step from on-disk rows.
5. **Idempotency.** Retries never create duplicate CRM records. Enforced via `external_ref` keys and the `WriteOperationResult.outcome` field.
6. **Observability.** Every decision, provider call, error, and duration is recorded in an append-only JSONL event log.
7. **Security by default.** No credentials in config files. Sensitive provider output (emails) is explicitly marked as `verified: bool`. Agent-returned content is validated as untrusted data.
8. **Minimal operational friction.** No Kubernetes, no message broker, no hosted dashboard for V1.

## Layers

```
┌─────────────────────────────────────────────────────┐
│ GTM Context / Brain                                 │
│ (YAML - ICP, personas, signal policy, exclusions,   │
│  operational policy)                                │
└──────────────────┬──────────────────────────────────┘
                   │ load_context()
                   ▼
┌─────────────────────────────────────────────────────┐
│ Agent Layer                                         │
│ (Qualifier protocol: DeterministicQualifier +       │
│  LLMQualifier for ambiguous cases)                  │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│ Workflow Layer                                      │
│ (AccountDiscoveryWorkflow orchestrator)             │
│ ├── DiscoveryProvider  ← fixture / real adapter     │
│ ├── EnrichmentProvider ← fixture / real adapter     │
│ ├── CRMAdapter         ← MockCRM / HubSpot / …       │
│ ├── Reconciliation     ← domain-canonical matching  │
│ ├── EnrichmentBudget   ← deterministic cents cap    │
│ ├── Policies           ← pre-approval hard checks   │
│ ├── Review artifact    ← Markdown for the approver  │
│ └── Sync + retry       ← revalidate then idempotent │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│ Persistence + Audit                                 │
│ ├── RunStore (SQLite)  ← per-run accounts, decisions,│
│ │   matches, costs, plan, write results             │
│ └── EventRecorder      ← append-only JSONL          │
└─────────────────────────────────────────────────────┘
```

## Why this shape

- **The brain layer is YAML, not a vector DB.** A vector DB is overkill when the entire context fits in one YAML file and the agent reads it in full. It would also make the context un-diffable, which defeats the "GTM as code" value prop.
- **The qualifier is a protocol, not a framework.** Two implementations ship: deterministic rules (default, offline) and `LLMQualifier`, which the workflow consults only when the rules return `insufficient_evidence`. Its Claude provider (`agents/anthropic_provider.py`) enforces call and spend caps in code and fails closed. Implementing a new one is 40 lines.
- **The budget is a plain `dataclass`, not a prompt.** The brief is explicit: budget limits must be enforced by deterministic code, not by relying on an LLM prompt.
- **Reconciliation happens before enrichment.** Enrichment calls cost money. If an account is already in the CRM, there is no reason to pay for it.
- **In-batch dedup matters.** Two rows from different providers may point at the same company. Catching this before sync avoids a race on the CRM's unique-domain constraint.
- **The review artifact is Markdown.** It can be opened by a human in a terminal, a GitHub PR, a Slack message, or anything that renders Markdown. No new UI to build.
- **Sync status is a state machine.** DRAFT → PROPOSED → (APPROVED | REJECTED) → (SYNCED | PARTIAL | FAILED). Illegal transitions raise; `SYNCED → sync` returns idempotent noops instead of erroring.

## Data flow (one `demo --offline` run)

1. `cli.demo` builds the workflow from on-disk YAML + SQLite files.
2. `discovery.discover(limit=50)` returns a `ProviderResult` with accounts, source failures, and per-provider coverage.
3. Each account is persisted, then handed to `qualifier.qualify()`.
4. Qualified accounts flow into `Reconciliation.reconcile()` against the CRM.
5. In-batch dedup overrides match type for later duplicate rows.
6. For `match_type == "none"` entries, the orchestrator asks `EnrichmentBudget.can_spend(cost)` before calling the provider.
7. Each entry is scored by `evaluate_entry()`; failures are attached to the entry, not surfaced as exceptions.
8. The full `WritePlan` is persisted at status `PROPOSED`.
9. `cli.approve` flips it to `APPROVED`.
10. `cli.sync` iterates entries and revalidates each one against the CRM first: a record already written under this run's `external_ref` is an `idempotent_noop`; a domain now owned by another writer is a `stale_plan` and nothing is written for it; otherwise the account is created. Retriable errors are retried up to 2 times.
11. Final status: `SYNCED` if nothing failed or went stale, `FAILED` if every write did, `PARTIAL` otherwise. Re-running `sync` on a `PARTIAL` plan finishes the failed writes and reports the rest as no-ops.
12. `evaluate_run()` runs 4 integrity checks and returns a report.

## Error model

- `CRMError(retriable=True)`: the sync loop retries.
- `CRMError(retriable=False)`: the sync loop records `failed` and moves on. The mock CRM raises this when a create would collide with a domain another writer owns, instead of silently returning that record.
- Pydantic validation errors at provider output: the entry is dropped before the plan is built (future work: route to a quarantine table).
- `ValueError` on illegal state transitions (approve-after-sync, sync-of-REJECTED, …).

## Where the line sits between deterministic and agentic

Deterministic:

- Context loading.
- Domain canonicalization.
- Reconciliation.
- Budget check.
- Policy evaluation.
- Review artifact rendering.
- Sync + retry.
- Audit trail.

Agentic (optional, off in offline mode):

- The qualifier, *only* for accounts the deterministic qualifier returned `insufficient_evidence` for.

Everything else is code. The LLM does not control budget, writes, or policy decisions. It can be asked to cast a tiebreaker vote, and its output is still validated as untrusted data before being accepted.

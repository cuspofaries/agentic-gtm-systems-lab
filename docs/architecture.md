# Architecture

One Python package, one orchestrator (`AccountDiscoveryWorkflow` in `src/gtm_lab/workflows/account_discovery.py`), one CLI. The six layers of [`concepts.md`](concepts.md) are boundaries inside it, not services.

## Execution order of one run

`gtm-lab demo` plans; `approve` and `sync` are separate commands. Boxes marked *(opt-in)* are off by default and paid when on; ◆ marks a control point enforced by code.

```
                         GTM Brain (gtm-context.yaml: ICP, personas, signal policy, offers,
                         pains, buying roles, research and action policy) is read by every step
plan (gtm-lab demo)
  1. discovery            fixture | PDL (opt-in)                                Data Layer
  2. signal search        Exa (opt-in, --signals exa)          ◆ signal budget  Data Layer
     accounts persisted with provenance, fields_missing, signal_search         Data Foundation
  3. ICP qualification    rules                                                 GTM Brain + code
       └─ undecided only  research loop (opt-in, --research exa) or LLM opinion (--agentic, never applied)
                          ◆ claims checked, reconciled; rules re-decide       Agentic Intelligence
  4. CRM reconciliation   canonical domain vs CRM, in-batch dedup               Data Layer
  5. opportunity          baseline (no AI) + investigator (opt-in, --opportunity exa)
     assessment           new qualified accounts; existing ones read-only with --opportunity-scope all
                          ◆ quotes, subject, semantic check, disconfirmation  Agentic Intelligence
  6. action policy        all_qualified (default) | opportunity_gated
                          ◆ fail closed: no checked assessment, no enrichment Activation & Governance
  7. enrichment           fixture | PDL, then FullEnrich / ZeroBounce (opt-in)
                          ◆ budget checked before each call, overcharge flagged
  8. policies             ◆ hard checks per entry                             Activation & Governance
  9. WritePlan PROPOSED + review artifact (+ opportunity brief)                 persisted
approve (gtm-lab approve)  ◆ a person, explicitly                             Activation & Governance
sync (gtm-lab sync)        ◆ portal identity, revalidation, idempotent writes Activation & Governance
eval (gtm-lab eval)        ◆ four integrity checks, exit 1 on failure         Evaluation
```

Every step writes events to `events.jsonl` and rows to `runs.sqlite`, so `gtm-lab inspect <run-id>` shows the whole run.

## Layers and code

| Layer | Code | Contracts |
|---|---|---|
| Data Foundation | `models.py`, `util.py`, `persistence/store.py`, `observability/events.py` | `Account`, `Signal`, `SourceAttribution`, `Confidence`, `SourcedClaim`, `FactResolution`, `Observation` |
| Data Layer | `providers/` (fixtures, `pdl.py`, `signals.py`, `email.py`), `crm/` (`mock.py`, `hubspot.py`), reconciliation in the workflow | `DiscoveryProvider`, `EnrichmentProvider`, `SignalProvider`, `EmailFinder`, `EmailVerifier`, `CRMAdapter`, `ProviderResult` |
| GTM Brain | `context/models.py`, `examples/account-discovery-to-crm/config/gtm-context.yaml` | `GTMContext`, `Offer`, `PainPoint`, `BuyingRole`, `SignalInterpretation`, `ResearchPolicy` |
| Agentic Intelligence | `agents/research_tools.py` (tools, budgets, loop), `agents/research.py`, `agents/opportunity.py`, `agents/anthropic_provider.py`, `agents/qualifier.py` | `QualificationDecision`, `OpportunityAssessment`, `SemanticPainMatch`, `DisconfirmationCheck` |
| Activation & Governance | `policies/opportunity_action.py`, `policies/rules.py`, `EnrichmentBudget` and sync in the workflow, `review.py`, `opportunity_brief.py` | `OpportunityAction`, `WritePlan`, `WritePlanEntry`, `WriteOperationResult` |
| Evaluation | `evaluations/checks.py`, `evaluations/opportunity_eval.py`, `evaluations/decision_eval.py`, `tests/scenarios/`, `tests/realcase/` | `EvalReport`, labeled cases in `evals/` |

## Who decides what

| Decided by Claude (only when an opt-in mode is on) | Decided by code |
|---|---|
| Which searches to run, which returned pages to read, when to stop | Which calls are allowed (URLs from a search or a known signal page only); search windows; LLM and search budgets before each call |
| Which facts or observations to report, with quotes and a stance | Whether a quote is in the page read and the page is about this company; whether a stated event date is in the quote |
| That a quote expresses a pain, in any wording | Whether that stands: an independent, constrained check sees only the quote and the pain |
| Which competing explanation to test | Whether the check counts: the search ran, named the company, every result was read |
| Its own proposed next action (recorded, not used) | ICP decision (rules), assessment status, next action, buying roles, whether contacts are bought, which personas, policy failures, writes |

Claude never decides the ICP, never calls a contact provider, never writes to the CRM. Its output becomes a plan and a brief that a person reviews.

## Design choices

- **Reconciliation before enrichment.** Paid calls happen only for accounts not already in the CRM.
- **Budgets are counters, not prompts.** `EnrichmentBudget` and `ResearchBudget` check the maximum cost before each call and book what the provider reports; a provider charging more than it announced is flagged, never clamped.
- **Fail closed.** Missing assessment, dropped claims, failed or exhausted research, unmapped roles, refusals, truncated answers: the outcome is "review" or "research more", never a paid action or a negative verdict.
- **The GTM Brain is YAML, not a vector store.** It fits in one file, is read in full and is reviewable as a diff.
- **The review artifact is Markdown.** It can be read in a terminal, a pull request or a chat message.
- **Sync is a state machine.** `DRAFT → PROPOSED → APPROVED | REJECTED → SYNCED | PARTIAL | FAILED`; illegal transitions raise; a second sync is all no-ops; a CRM that changed after approval gives `stale_plan` and nothing is written for that entry.
- **Replays are keyed on the prompt.** Recorded model answers are looked up by case, turn, last message and a fingerprint of the prompt and tools, so a prompt change misses instead of replaying stale answers.

## What this architecture is not

- Not a declarative workflow engine: the order of steps is Python code. The YAML configures what the steps use (ICP, offers, policies), not which steps run. A YAML pipeline with `plan` and `diff` commands is a possible future direction, not a feature.
- Not provider-agnostic at runtime: adapters share interfaces, but each one is selected by a CLI flag and wired in `cli.py`. Claude is the only model the agentic modes call.
- Not a hosted platform, a scheduler or a multi-tenant service: one CLI process, one SQLite state directory.

## Error model

- `CRMError(retriable=True)`: retried by the sync loop. `retriable=False`: recorded as `failed`, sync moves on.
- Provider failures are returned in `source_failures`, never raised into the plan.
- LLM errors, refusals and truncated answers fail closed to `insufficient_evidence` with the reason in `missing`.
- Illegal plan transitions (`approve` after `sync`, `sync` of a rejected plan) raise `ValueError`.

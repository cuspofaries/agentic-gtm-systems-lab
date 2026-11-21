# agentic-gtm-systems-lab

**Build, run, test and debug real-world Agentic GTM workflows.**

An executable reference architecture for modern GTM engineering - from company context and data infrastructure to agent decisions, CRM actions, and evaluation.

[![tests](https://github.com/cuspofaries/agentic-gtm-systems-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/cuspofaries/agentic-gtm-systems-lab/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

---

## What business problem does this solve?

Most "AI GTM agent" repos stop at **discovery** (find companies) or **analysis** (why we lost). This one covers **the write path** - the slice of the workflow where an agent actually proposes changes to your CRM, and where things go wrong and cost money.

Specifically: an end-to-end **Account Discovery → ICP Qualification → CRM Reconciliation → Enrichment (budget-enforced) → Human Approval → Idempotent Sync** pipeline, in a Python repo you can clone and run offline in five minutes.

The four guardrails this repo exists to demonstrate:

1. **CRM reconciliation before enrichment** - never pay to enrich an account you already have.
2. **Deterministic budget enforcement** - a cents counter, not a prompt that asks the LLM nicely.
3. **Mandatory human approval** between agent proposal and CRM write.
4. **Idempotent, resumable sync** - retrying a run never creates duplicate records, and a plan whose CRM state changed after approval is not applied blindly.

## Who is it for?

**Developers learning GTM Engineering** - run a real workflow locally, break it on purpose, watch how failures propagate through a safe pipeline.

**Experienced GTM Engineers** - extract reusable components (the budget enforcer, the reconciliation layer, the policy evaluator, the write-plan review artifact) and drop them into your existing Clay / n8n / Python stack. No platform adoption required.

## What can I run in five minutes?

```bash
git clone https://github.com/cuspofaries/agentic-gtm-systems-lab
cd agentic-gtm-systems-lab
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

gtm-lab demo --offline        # Full pipeline; stops at the human approval gate
gtm-lab runs                  # list the run id it just created
gtm-lab approve <run-id>      # promote plan to APPROVED
gtm-lab sync <run-id>         # apply to the mock CRM (run it twice: the second run is all no-ops)
gtm-lab eval <run-id>         # run 4 integrity checks

pytest -q                     # 47 tests pass
```

## What happens during the example workflow?

```
          +--------------------+
          |  GTM Context       |   YAML - ICP, personas, signals, exclusions, policies
          |  (gtm-context.yaml)|
          +--------------------+
                    |
                    v
          +--------------------+   DiscoveryProvider → ProviderResult
          |  1. Discovery       |   (accounts, source_failures, search_coverage)
          +--------------------+
                    |
                    v
          +--------------------+   DeterministicQualifier: rules-based decision
          |  2. Qualification   |   with explicit confidence + evidence + missing
          +--------------------+
                    |
                    v
          +--------------------+   Domain-canonicalized match against CRM state
          |  3. Reconciliation  |   + in-batch dedup; ambiguous cases flagged
          +--------------------+
                    |
                    v
          +--------------------+   EnrichmentBudget enforces a cents cap
          |  4. Enrichment      |   BEFORE any provider call is made
          +--------------------+
                    |
                    v
          +--------------------+   Policies catch: weak evidence, unsupported
          |  5. Policy evaluation|  agent claims, required-email missing, etc.
          +--------------------+
                    |
                    v
          +--------------------+   Markdown review artifact; nothing writes
          |  6. Human approval  |   until approve or reject is called
          +--------------------+
                    |
                    v
          +--------------------+   Revalidate against current CRM state,
          |  7. Sync            |   then idempotent writes (external_ref keys)
          +--------------------+   with retry on retriable CRMError
                    |
                    v
          +--------------------+   SQLite run store + JSONL event log
          |  8. Audit trail     |   gtm-lab eval runs 4 integrity checks
          +--------------------+
```

Example output from `gtm-lab demo --offline`:

```
Plan proposed: run_id=run_
Review artifact → .gtm-lab-state/review_run_.md

                                    Entries
┌─────────────┬────────────────┬──────────┬─────────────────┬─────────────────┐
│ Account     │ Action         │ Contacts │ Enrichment      │ Policy failures │
├─────────────┼────────────────┼──────────┼─────────────────┼─────────────────┤
│ acc_001     │ create_account │ 2        │ done            │ -               │
│ acc_003     │ skipped        │ 0        │ not_needed      │ -               │
│ acc_004     │ create_account │ 1        │ done            │ -               │
│ acc_004_dup │ skipped        │ 0        │ not_needed      │ -               │
│ acc_006     │ create_account │ 0        │ done            │ -               │
│ acc_007     │ create_account │ 1        │ done            │ -               │
│ acc_008     │ create_account │ 1        │ done            │ -               │
│ acc_009     │ create_account │ 0        │ budget_exceeded │ -               │
│ acc_010     │ create_account │ 0        │ budget_exceeded │ -               │
└─────────────┴────────────────┴──────────┴─────────────────┴─────────────────┘
```

What this tells you: two entries are skipped (one already in the CRM, one duplicate of another row in the same batch) and cost nothing to enrich. The last two were never enriched because the 50¢ budget ran out, and the table says so instead of showing an empty result. `acc_002` (an agency) and `acc_005` (no headcount) are absent because they did not qualify.

## Agentic mode (optional)

```bash
export ANTHROPIC_API_KEY=...       # or `ant auth login`
gtm-lab demo --agentic --max-llm-calls 5 --max-llm-cents 50
```

Claude (`claude-opus-5-5`) is consulted for one thing only: accounts the rules returned as `insufficient_evidence`. Everything around that call is code:

- hard caps on the number of calls and on estimated spend, checked before each call;
- account data is sent as untrusted data inside an `<account_data>` block;
- the answer comes back as structured output and is validated; a refusal, an API error or a cap hit becomes `insufficient_evidence`;
- an LLM `qualified` still goes through the same policy checks, so evidence that does not point at an observable field blocks approval;
- each LLM decision is logged next to the deterministic baseline, and the demo prints the comparison.

Without credentials, `--agentic` says so and runs the offline mode. The LLM path is unit-tested with a fake client; it has **not** been run against the live API in CI (no key there).

## Which components can I reuse?

Everything under `src/gtm_lab/` is modular. The pieces most people want to steal:

| Component | What it does | Drop-in location |
|---|---|---|
| `EnrichmentBudget` | Cents-based deterministic budget enforcer | Any Python workflow calling a paid provider |
| `canonicalize_domain` | Domain normalizer for deduping | Any CRM reconciliation layer |
| `Reconciliation` | Domain-based match against a CRM, with ambiguity flagged | Any CRM-writing pipeline |
| `evaluate_entry` (policies) | 5 hard checks on a proposed write | Before any CRM POST |
| `render_review` | Markdown review artifact for human approval | Any workflow needing a review gate |
| `evaluations.evaluate_run` | 4 post-run integrity checks | After any sync |
| `MockCRM` | SQLite-backed, idempotent, fault-injectable | Dev / CI environments |

See [`docs/integration-guide.md`](docs/integration-guide.md) for recipes.

## How does the system handle failures?

13 named failure scenarios, each covered by a test in `tests/scenarios/test_scenarios.py`. See [`docs/failure-scenarios.md`](docs/failure-scenarios.md) for the catalogue.

Key guarantees:

- **Transient CRM write failures** → automatic retry (bounded).
- **Interrupted mid-sync** → state survives in SQLite; a new process re-runs `sync`, finishes the failed writes and reports the rest as no-ops.
- **CRM changed after approval** → the entry is reported `stale_plan` and nothing is written for it; it needs a fresh plan.
- **Duplicate inputs** → in-batch dedup marks later rows as ambiguous, no blind write.
- **Missing data** → `insufficient_evidence` decision, never silently guessed.
- **Unsupported agent claims** → policy rejects before approval is offered.

## How do I integrate real providers?

Each provider is an interface (`DiscoveryProvider`, `EnrichmentProvider`, `CRMAdapter`). Implement the methods against your actual vendor and inject the class into `AccountDiscoveryWorkflow`. The orchestrator doesn't know or care which provider is behind the interface.

See [`docs/integration-guide.md`](docs/integration-guide.md) for an outline Clay / Apollo / HubSpot / Salesforce adapter.

## What are the limitations?

- **Offline demo only out of the box.** No live provider adapter is bundled. The brief says this is intentional - the point is the pipeline shape, not vendor lock-in.
- **The agentic path has not been run against the live API in CI.** It is unit-tested with a fake client. There is no labeled benchmark yet, so the LLM-vs-baseline comparison shows agreement, not accuracy.
- **Mock CRM only.** A HubSpot or Salesforce adapter must be added before this touches a real CRM. The brief explicitly calls that out as a stretch goal, not part of V1.
- **No multi-tenant state.** One SQLite file per workflow run.
- **No scheduler / queue.** `gtm-lab` is CLI-driven; you orchestrate when it runs.

## What should I explore next?

- Add a real `DiscoveryProvider` (start with a Crunchbase / BuiltIn fixture → live API).
- Implement a HubSpot or Salesforce `CRMAdapter` reusing the external_ref idempotency pattern.
- Extend `evaluate_entry` with your organization's own hard policies.
- Write your own `GTMContext` in `config/gtm-context.yaml` for your actual ICP.

## Repository layout

```
agentic-gtm-systems-lab/
├── src/gtm_lab/
│   ├── context/                  ← YAML loader + Pydantic context models
│   ├── providers/                ← DiscoveryProvider / EnrichmentProvider + fixtures
│   ├── crm/                      ← CRMAdapter + SQLite mock
│   ├── agents/                   ← DeterministicQualifier + LLMQualifier + Claude provider
│   ├── workflows/                ← Orchestrator + Reconciliation + EnrichmentBudget
│   ├── policies/                 ← Hard policy checks before approval
│   ├── persistence/              ← SQLite run state store
│   ├── observability/            ← JSONL event recorder
│   ├── evaluations/              ← Post-run integrity checks
│   ├── review.py                 ← Markdown review artifact renderer
│   └── cli.py                    ← Typer CLI (6 commands)
├── examples/account-discovery-to-crm/
│   ├── config/gtm-context.yaml
│   └── fixtures/{discovery,enrichment,crm-seed}.yaml
├── tests/
│   ├── unit/                     ← 31 unit tests
│   ├── integration/              ← 3 end-to-end tests (incl. no-network check)
│   └── scenarios/                ← 13 named failure scenarios
├── docs/
│   ├── architecture.md
│   ├── concepts.md
│   ├── failure-scenarios.md
│   ├── integration-guide.md
│   └── adr/                      architectural decision records
├── .github/workflows/ci.yml      ← lint + test + offline scenario
└── pyproject.toml
```

## Capabilities table

| Capability | Status |
|---|---|
| Account discovery (fixture) | Implemented and tested |
| ICP qualification (rules) | Implemented and tested |
| ICP qualification (LLM, ambiguous cases only) | Implemented, unit-tested with a fake client, not run live in CI |
| CRM reconciliation | Implemented and tested |
| Enrichment budget | Implemented and tested |
| Human approval gate | Implemented and tested |
| Idempotent CRM sync + revalidation after approval | Implemented and tested (mock CRM) |
| State persistence | Implemented and tested |
| Audit trail | Implemented and tested |
| 13 failure scenarios | All 13 tested and green |
| HubSpot / Salesforce adapters | Planned - not implemented |
| Labeled benchmark for the LLM step | Planned - not implemented |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).

## Attribution

Pattern references are inline in module docstrings where relevant. No code is copied from third-party repositories.

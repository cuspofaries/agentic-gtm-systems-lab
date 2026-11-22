# agentic-gtm-systems-lab

**Build, run, test and debug real-world Agentic GTM workflows.**

An executable reference architecture for modern GTM engineering - from company context and data infrastructure to agent decisions, CRM actions, and evaluation.

[![tests](https://github.com/cuspofaries/agentic-gtm-systems-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/cuspofaries/agentic-gtm-systems-lab/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

![Terminal recording: gtm-lab demo proposes a plan, approve, sync writes 12 records, a second sync writes nothing, eval passes its 4 checks](docs/assets/demo.gif)

*The offline demo end to end: plan, approval, sync, a second sync that is a no-op, and the integrity checks. Real command output; only the typing is simulated.*

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

Two entry points on the same code.

### Option A: Claude Code (guided)

```bash
git clone https://github.com/cuspofaries/agentic-gtm-systems-lab.git
cd agentic-gtm-systems-lab
claude
```

Then type `/gtm-lab`. Claude Code introduces the lab, runs the offline demo, explains each decision from the persisted data, and walks you through approval and sync, asking before every state change. Choose this to learn the workflow or to investigate a run conversationally. See [`docs/claude-code.md`](docs/claude-code.md).

### Option B: Standalone CLI

```bash
git clone https://github.com/cuspofaries/agentic-gtm-systems-lab.git
cd agentic-gtm-systems-lab
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

gtm-lab demo --offline        # Full pipeline; stops at the human approval gate (add --json for structured output)
gtm-lab runs                  # list the run id it just created
gtm-lab approve <run-id>      # promote plan to APPROVED
gtm-lab sync <run-id>         # apply to the mock CRM (run it twice: the second run is all no-ops)
gtm-lab eval <run-id>         # run 4 integrity checks

pytest -q                     # 77 tests pass (1 live HubSpot test skipped unless opted in)
```

Choose this for scripts, CI, or to reuse the Python components without Claude Code.

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
Plan proposed: run_id=run_20261121T215033Z
Review artifact → .gtm-lab-state/review_run_20261121T215033Z.md

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

- hard caps on the number of calls and on spend: a call starts only if its worst-case cost still fits;
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

A HubSpot adapter ships: `gtm-lab demo --crm hubspot`, then approve and sync as usual. It was run end to end against a HubSpot test portal; see [`docs/hubspot.md`](docs/hubspot.md) for setup and the HubSpot behaviors it has to work around. See [`docs/integration-guide.md`](docs/integration-guide.md) for outlines of other adapters (Clay, Apollo, Salesforce).

## What are the limitations?

- **Offline by default.** The demo uses fixtures and a mock CRM; real providers (HubSpot, People Data Labs) are opt-in flags.
- **The agentic path has not been run against the live API in CI.** It is unit-tested with a fake client. There is no labeled benchmark yet, so the LLM-vs-baseline comparison shows agreement, not accuracy.
- **Real providers: HubSpot (CRM) and People Data Labs (companies, contacts).** Salesforce is not implemented. On PDL's free plan, contacts come without emails.
- **Idempotency is per run, not universal.** Retrying or re-syncing a run never duplicates. Two different runs (or another tool) writing the same new company are separated only by a domain search, and HubSpot's search lags writes by a few seconds: run one sync at a time per portal (see `docs/hubspot.md`).
- **No multi-tenant state.** One SQLite file per workflow run.
- **No scheduler / queue.** `gtm-lab` is CLI-driven; you orchestrate when it runs.

## What should I explore next?

- Add a real `DiscoveryProvider` (start with a Crunchbase / BuiltIn fixture → live API).
- Implement a Salesforce `CRMAdapter` on the same pattern as `crm/hubspot.py` (unique external ref, revalidation before each write).
- Extend `evaluate_entry` with your organization's own hard policies.
- Write your own `GTMContext` in `config/gtm-context.yaml` for your actual ICP. Every setting, its demo value and its effect are listed in [`docs/configuration.md`](docs/configuration.md). The demo values (50¢ budget, 10¢ per call, 5 LLM calls) only exist to trigger the test scenarios; they are not recommendations.

## Repository layout

```
agentic-gtm-systems-lab/
├── src/gtm_lab/
│   ├── context/                  ← YAML loader + Pydantic context models
│   ├── providers/                ← DiscoveryProvider / EnrichmentProvider + fixtures
│   ├── crm/                      ← CRMAdapter + SQLite mock + HubSpot adapter
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
│   ├── unit/                     ← 49 unit tests (incl. HubSpot adapter against a fake)
│   ├── integration/              ← 15 tests: end-to-end, no-network, CLI and Claude Code skill checks
│   ├── scenarios/                ← 13 named failure scenarios
│   └── live/                     ← opt-in test against a real HubSpot portal
├── docs/
│   ├── architecture.md
│   ├── concepts.md
│   ├── configuration.md          every setting, its default and what it does
│   ├── claude-code.md            the /gtm-lab skill: commands, safety, real transcripts
│   ├── hubspot.md                HubSpot connector: setup, behavior, limits
│   ├── pdl.md                    People Data Labs discovery and contacts
│   ├── failure-scenarios.md
│   ├── integration-guide.md
│   └── adr/                      architectural decision records
├── .claude/skills/gtm-lab/       ← /gtm-lab Claude Code skill
├── CLAUDE.md                     ← orientation for Claude Code
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
| Claude Code skill `/gtm-lab` | Implemented; files checked in CI; invoked for real in Claude Code print mode (not the interactive UI) |
| Structured CLI output (`--json`, `inspect`) | Implemented and tested |
| HubSpot adapter (companies, contacts) | Implemented; unit-tested against a fake in CI; run end to end against a HubSpot test portal (opt-in live test) |
| People Data Labs discovery and contacts | Implemented; unit-tested against a fake in CI; run end to end with HubSpot on real companies (free plan: no emails) |
| Salesforce adapter | Planned - not implemented |
| Labeled benchmark for the LLM step | Planned - not implemented |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).

## Attribution

Pattern references are inline in module docstrings where relevant. No code is copied from third-party repositories.

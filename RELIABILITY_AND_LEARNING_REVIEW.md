# Reliability and learning review

Branch `fix/reliability-transparency`. Audited HEAD: `87eef4141889540201b3398fd7d5b84dcce9f981` (2026-11-22).

The positioning stays "Learn, build and test Agentic GTM systems: from data foundations to autonomous decision-making." (with a colon: the maintainer does not allow a long dash in the repository).

## 1. Issues confirmed and fixed

### P0: execution observability and cost reporting

**Confirmed.** `summary.run_summary` set `mode` from `workflow.start.llm_fallback_configured` and `llm_calls_made` from the number of `qualification.fallback` events. A run with `--opportunity exa` and no `--agentic` was therefore reported as `offline` with 0 LLM calls, although Claude and Exa ran. A configured `--agentic` with nothing to decide was reported as `agentic`.

**Fixed.** At the end of `plan()`, the workflow records an `execution.summary` event (`src/gtm_lab/execution.py`), built from the run's persisted outcome and the components' own counters. Per component (ICP rules, LLM opinion, company research, opportunity intelligence, signal discovery, contact enrichment, CRM): `configured`, `executed`, `accounts`, calls, provider and model, cost with the unit in the key (`*_cents`, `*_millidollars`), failures and skipped work, and `kind`: `deterministic`, `simulated` (fixtures, mock CRM), `live`, `replayed` or `recorded`. Totals keep units apart (`llm_cost_cents_total`, `exa_cost_millidollars_total`, `contact_cost_cents_total`). `workflow.start` also records what was configured.

`gtm-lab demo --json` and `gtm-lab inspect` read it back: `mode` is now `offline` (nothing called a real service), `agentic` (Claude called live), `agentic_replayed` (recorded answers) or `live_providers` (real providers or CRM, no Claude); `llm_calls_made` is the total of calls actually made; a new `execution` key holds the details. The human demo output prints one execution line; the opportunity brief prints the opportunity and enrichment usage. Runs recorded before this change are flagged `execution.legacy` and keep the old reading. The evaluation harness and the real-case harness mark their components `replayed` or `recorded`.

### P0: ICP qualification boundary

**Confirmed.** `LLMQualifier` (`--agentic`) returned `qualified` or `not_qualified` for accounts the rules had found insufficient, from the same account data and no newly checked fact. A `not_qualified` needed no evidence at all.

**Fixed.** An `--agentic` verdict is now a proposal: the decision is always `insufficient_evidence`, the grounded evidence is kept, and the verdict is recorded in `missing` as `llm_proposal_not_applied:<verdict>` (`agents/qualifier.py`). Missing facts are filled only by the research loop (`--research exa`), whose claims are checked against the pages, reconciled per field, and re-decided by the rules on a copy of the account (`decided_by: hybrid`, unchanged). The only producers of `qualified` / `not_qualified` are the deterministic rules.

Coverage of the brief's cases: LLM `qualified` with unsupported facts and `not_qualified` without verified evidence (`tests/unit/test_agentic.py`); conflicting and missing employee counts, facts that genuinely qualify or disqualify, provider failure (`tests/unit/test_research_loop.py`); stale signals (`tests/scenarios/test_scenarios.py::test_s06`); replay of recorded decisions (`tests/integration/test_real_case_harness.py`, `tests/integration/test_opportunity_eval.py`).

### P1: learning path continuously tested

**Added** `tests/integration/test_learning_path.py`: each module of `docs/learning-path.md` replayed offline in a temporary state directory against the mock CRM and the committed recordings, checking the outcomes the page announces (unknown fields, identity and duplicates, reconciliation, ICP change and GTM Brain validation, a supported and a disconfirmed replayed case, label edits not breaking replay, gated mode spending nothing offline, approval and idempotent sync, `eval`, a prompt change missing the recording). Two meta-tests fail if the page quotes a `gtm-lab` option or a `pytest -k` selection that no longer exists. Each module gained "why it matters to a GTM team" and "where it lives"; shell requirements are stated.

Not automated: the shell plumbing (`RUN=$(...)`, `cat` of the review file); the commands behind it are.

### P1: documentation consistency

Updated to the new behavior: `--agentic` semantics (README, `CLAUDE.md`, `docs/concepts.md`, `docs/configuration.md`, `docs/architecture.md`, `docs/research.md`, `docs/claude-code.md`, the `/gtm-lab` skill), how execution and cost are reported (README, `CLAUDE.md`, the skill). Checked and found consistent: who decides the ICP (rules only), what Claude can do (research and proposals, never writes or spending decisions), which features are optional and paid, which connectors ship, what provider independence means (interfaces, not runtime swapping), which evaluations are fictional and which were recorded on unseen cases, and that CRM writes always need a human approval.

## 2. Files changed

`src/gtm_lab/execution.py` (new), `workflows/account_discovery.py`, `summary.py`, `agents/qualifier.py`, `opportunity_brief.py`, `cli.py`, `evaluations/opportunity_eval.py`; tests `tests/unit/test_execution.py` (new), `tests/integration/test_learning_path.py` (new), `tests/unit/test_agentic.py`, `tests/realcase/harness.py`; docs listed above.

## 3. Compatibility

- `demo --json` / `inspect`: `mode` can take two new values (`agentic_replayed`, `live_providers`) and now reflects execution; `llm_calls_made` counts calls rather than fallback events; `execution` is a new key. Other keys unchanged.
- `--agentic` no longer changes decisions. A script relying on it to qualify accounts must use `--research exa`.
- No CLI option removed or renamed. Evaluation cases, labels and recordings unchanged; all replays produce the same numbers as before this change.

## 4. Tests and CI

`ruff check .` clean; `pytest`: 254 passed, 2 skipped (opt-in live HubSpot, private real-data case). `gtm-lab demo --offline` (execution line: offline, 0 LLM calls), `gtm-lab opportunity-eval`, `gtm-lab decision-eval` and `gtm-lab decision-eval --cases evals/decisions/fresh.yaml --cassette evals/decisions/fresh-cassette.json` replay without a miss and give the same results as on `main`. No paid or network call was made; CI runs fixtures and replays only.

## 5. Remaining limitations

- `kind` is declared by components (fixture and mock classes are `simulated`; harnesses mark replays); a custom live component that wraps a fake is reported `live` unless it sets `execution_kind`.
- Costs are what providers report or the documented price; there is no reconciliation with provider invoices.
- CRM writes are reported by sync results, not by `execution.summary` (which covers planning).
- The evaluations remain small, fictional and single-labeled.

## 6. Next priorities

1. Run the decision comparison on real accounts labeled independently, through the private record/replay harness (paid, needs approval).
2. Record sync-time CRM writes in the same execution format.
3. Re-record the private real-data research case (its recording predates typed claims).

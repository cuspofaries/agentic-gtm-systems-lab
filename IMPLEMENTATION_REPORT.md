# Implementation report - agentic-gtm-systems-lab V1

*2026-11-21 · cuspofaries*

This report covers V1. The Claude Code integration added afterwards is described in `CLAUDE_CODE_EVOLUTION_REPORT.md`.

## 1. Executive summary

`pip install -e ".[dev]"` then `gtm-lab demo --offline` runs the full Account Discovery → ICP Qualification → CRM Reconciliation → Enrichment (budget-enforced) → Human Approval → Sync pipeline on a synthetic fixture in a few seconds. Sync revalidates every entry against the current CRM before writing, so a re-run is a reported no-op and a plan whose CRM state changed after approval is not applied blindly.

47 pytest tests pass, including the 12 failure scenarios from the brief plus a 13th for "CRM changed after approval". An optional agentic mode sends only the ambiguous accounts to Claude (`claude-opus-5-5`) under hard call and spend caps. That path is tested with a fake client and has not been run against the live API.

The code is plain Python (Pydantic, SQLite, Typer). No LangChain, Temporal or Docker.

## 2. Architecture implemented

Nine modules under `src/gtm_lab/`:

- `context/` - YAML loader + Pydantic models (ICP, personas, signal policy, exclusions, operational policy)
- `providers/` - `DiscoveryProvider` and `EnrichmentProvider` interfaces + fixture implementations
- `crm/` - `CRMAdapter` contract + SQLite `MockCRM` (idempotent on `external_ref`, raises on domain conflicts, fault injection for tests)
- `agents/` - `DeterministicQualifier` (rules), `LLMQualifier` (validates any model output), `AnthropicProviderCall` (Claude, structured output, caps)
- `workflows/` - `AccountDiscoveryWorkflow` orchestrator + `Reconciliation` + `EnrichmentBudget`
- `policies/` - five hard checks run before approval is offered
- `persistence/` - SQLite run store (accounts, decisions, matches, costs, plan, write results)
- `observability/` - append-only JSONL event log
- `evaluations/` - four post-run integrity checks

Plus `cli.py` (Typer: `demo`, `eval`, `runs`, `inspect`, `approve`, `reject`, `sync`), `review.py` (Markdown artifact for the approver), `models.py`, `util.py`.

See `docs/architecture.md`.

## 3. Actual workflow execution results

A clean `gtm-lab demo --offline` on the bundled fixture:

- 11 accounts discovered (one provider failure recorded in the fixture).
- 9 qualified, 1 not qualified (agency), 1 insufficient evidence (no headcount).
- 1 skipped as already in the CRM, 1 skipped as an in-batch duplicate.
- 5 enrichment calls, exactly at the 50¢ cap; the 2 accounts after the cap are marked `budget_exceeded`.
- 7 approvable entries, 0 policy blockers.
- After approve + sync: 8 CRM accounts (1 seeded + 7 new), 5 contacts, 0 failures.
- Second sync: every operation reported `idempotent_noop`, CRM unchanged.

Exact figures: `examples/account-discovery-to-crm/expected-results/summary.md`.

## 4. Test results

```
$ pytest -q
47 passed
```

- 31 unit tests: utilities, qualifier, budget, mock CRM, reconciliation, agentic path (routing, call cap, spend cap, refusal, client error, policies applied to LLM output).
- 3 integration tests: end-to-end numbers, re-sync is a reported no-op, and the offline workflow with sockets disabled.
- 13 scenario tests: S01 to S13 below.

CI runs lint, the suite, the offline demo with approve, two syncs and eval, and `--agentic` without credentials (must fall back and say so).

## 5. Failure scenarios verified

| # | Scenario | Status |
|---|---|---|
| S01 | ICP match qualifies and is approvable | ✅ |
| S02 | Non-ICP rejected by exclusion rule | ✅ |
| S03 | Already in CRM: skipped, not enriched | ✅ |
| S04 | In-batch duplicate marked ambiguous | ✅ |
| S05 | Missing headcount → insufficient_evidence | ✅ |
| S06 | Stale signal not used as evidence | ✅ |
| S07 | Raised evidence floor demotes accounts without a fresh signal | ✅ |
| S08 | Contact without email proposed and synced as-is | ✅ |
| S09 | Enrichment budget enforced, entries marked `budget_exceeded` | ✅ |
| S10 | Transient CRM write retried, succeeds on attempt 2 | ✅ |
| S11 | Sync interrupted (PARTIAL), finished by a new process | ✅ |
| S12 | Human rejection prevents sync | ✅ |
| S13 | CRM changed after approval → `stale_plan`, nothing written | ✅ |

See `docs/failure-scenarios.md`.

## 6. Reusable components

1. `EnrichmentBudget` - cents counter (`workflows/account_discovery.py`)
2. `canonicalize_domain` - domain normalizer (`util.py`)
3. `Reconciliation` - CRM match + in-batch dedup
4. `evaluate_entry` - hard policy checks
5. `render_review` - Markdown approval artifact
6. `evaluate_run` - four integrity checks for CI
7. `MockCRM` - idempotent SQLite CRM for dev and tests
8. `AnthropicProviderCall` - capped, fail-closed LLM call returning a structured decision

Recipes in `docs/integration-guide.md`.

## 7. Integration limitations

- **No live data-provider adapter ships.** A HubSpot CRM adapter was added after V1 (`docs/hubspot.md`); Clay, Apollo and Salesforce remain next steps.
- **The agentic path has not run against the live API.** It is unit-tested with a fake client. There is no labeled benchmark, so the LLM-vs-baseline comparison reports agreement, not accuracy.
- **Single-writer SQLite.** Concurrent runs on the same state directory are not supported.
- **No scheduler.** The CLI is one-shot; wrap it in cron, systemd or an orchestrator if needed.
- **Synthetic contacts only.** Fixture emails use `example.com` (reserved by RFC 2606).
- **Examples are not packaged.** `gtm-lab demo` expects a source checkout (`pip install -e .`).

## 8. Security considerations

- No secrets in config. Offline mode needs none; agentic mode reads standard Anthropic credentials from the environment or an `ant auth login` profile.
- `require_approval_before_crm_write` is typed `Literal[True]`: the human gate cannot be turned off through config.
- Sync revalidates before writing and the mock CRM refuses to write into a domain owned by another record, so contacts are never attached to someone else's account.
- Provider and account data sent to the LLM are marked as untrusted data; model output is schema-validated, then still passes the same policy checks as rules output.
- LLM calls and spend are capped in code; a cap hit, refusal or API error becomes `insufficient_evidence`.
- The offline workflow is tested with sockets disabled.

## 9. Technical debt

- An enrichment provider failure is recorded (`provider_failed`) but not quarantined for operator review.
- Retry is bounded and immediate, with no backoff.
- A `stale_plan` entry needs a manual re-plan; there is no `gtm-lab replan` helper.
- Server-side refusal fallbacks are not enabled on the LLM call: a refusal is treated as `insufficient_evidence` and goes to a human, which is the safe default here.

## 10. Recommended next improvements

1. ~~A HubSpot `CRMAdapter`~~: done after V1, see `docs/hubspot.md`.
2. A real `DiscoveryProvider` for one public source.
3. A small labeled benchmark (20 to 30 ambiguous accounts with agreed answers) to measure the LLM step instead of only logging agreement.
4. Quarantine table for provider failures.
5. `gtm-lab replan <run-id>` for stale entries.

## 11. GitHub repository

https://github.com/cuspofaries/agentic-gtm-systems-lab (Apache-2.0). CI runs on every push.

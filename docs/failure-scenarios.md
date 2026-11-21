# Failure scenarios

The brief lists 12 named scenarios the workflow must handle correctly, plus the requirement that a plan is not applied blindly if the CRM changed after approval (S13). Each one is covered by a test in `tests/scenarios/test_scenarios.py` and exercised by the `examples/account-discovery-to-crm/` fixture.

| # | Scenario | Fixture account | Test | Expected outcome |
|---|---|---|---|---|
| S01 | ICP match | `acc_001` | `test_s01_icp_match_qualifies` | `qualified`, enriched, approvable, syncs |
| S02 | Non-ICP (wrong industry) | `acc_002` | `test_s02_non_icp_rejected` | `not_qualified`, no enrichment |
| S03 | Already in CRM | `acc_003` | `test_s03_already_in_crm_skipped` | `skipped`, reason "Already exists in CRM" |
| S04 | Duplicate rows in batch | `acc_004` + `acc_004_dup` | `test_s04_duplicate_in_batch_marked_ambiguous` | second row `skipped`, reason "Ambiguous" |
| S05 | Missing employee data | `acc_005` | `test_s05_missing_employee_data_yields_insufficient_evidence` | `insufficient_evidence` |
| S06 | Stale buying signal | `acc_006` | `test_s06_stale_signal_is_not_counted_as_evidence` | qualifies on ICP; signal NOT in evidence |
| S07 | Weak evidence (< policy min) | `min_evidence_count` raised to 4 | `test_s07_weak_evidence_not_qualified` | accounts without a fresh signal (3 facts) become `insufficient_evidence`; only `acc_001` stays approvable |
| S08 | Incomplete contact (no email) | `acc_008` | `test_s08_incomplete_contact_handled_gracefully` | contact proposed and synced with `email=None`, not dropped silently |
| S09 | Enrichment budget exceeded | `acc_009`, `acc_010` | `test_s09_enrichment_budget_enforced` | provider not called; entries marked `budget_exceeded` with 0 contacts |
| S10 | Transient CRM write fails | fault injection | `test_s10_transient_crm_write_failure_is_retried` | succeeds on attempt 2; plan `SYNCED` |
| S11 | Sync interrupted, resumed by a new process | 3 injected failures on `acc_001` | `test_s11_interrupted_sync_resumes_in_new_process` | first sync `PARTIAL`; a fresh workflow over the same files creates `acc_001`, reports the rest as no-ops, ends `SYNCED` with 8 accounts |
| S12 | Human rejects the plan | `reject` call | `test_s12_human_rejection_prevents_sync` | `sync` raises `ValueError`; CRM untouched |
| S13 | CRM changed after approval | `brightpath` created by another writer | `test_s13_crm_changed_after_approval_is_not_overwritten` | `stale_plan`; no account or contact written for it; plan `PARTIAL` |

## How failures propagate

| Failure site | Default behavior | Where it surfaces |
|---|---|---|
| Discovery provider returns nothing | Empty run; plan has 0 entries | `source_failures` on `ProviderResult` + event log |
| Qualifier can't decide | `insufficient_evidence` decision | Decision persisted; account not in plan |
| Reconciliation finds two CRM hits | `match_type = "ambiguous"` | Entry marked `skipped` with reason |
| Enrichment provider errors | Caught; `source_failures` on result | Event log + no contacts in entry |
| Enrichment budget exceeded | Call not made | Event log; entry has 0 contacts |
| Policy rejects an entry | `policy_failures` list on entry | Review artifact shows the reason |
| CRM write - retriable error | Retried up to 2 times | `WriteOperationResult.attempt` > 1 |
| CRM write - non-retriable | `outcome = "failed"` | Event log; plan may become `PARTIAL` |
| CRM changed after approval | `outcome = "stale_plan"`, nothing written for the entry | Event log + write results; plan `PARTIAL` |
| Plan interrupted mid-run | Next process loads from SQLite | No duplicate work; state machine resumes |
| Human rejects | Plan status = `REJECTED` | `sync` refuses to run |

## Scenarios we deliberately do NOT cover in V1

- Multi-tenant state (one SQLite file per lab).
- Rate-limited retry with backoff (bounded retry only).
- Partial contact creation across accounts (all-or-partial at the plan level, not sub-account).
- Live LLM failure modes beyond what a fake client can simulate (refusal, API error, cap hit are tested; real model behavior is not).
- A real CRM's eventual-consistency window (the mock CRM is strongly consistent).

Each of these is called out as future work in `IMPLEMENTATION_REPORT.md`.

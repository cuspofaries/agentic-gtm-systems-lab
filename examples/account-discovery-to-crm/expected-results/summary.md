# Expected results for the account-discovery-to-crm example

These are the exact numbers any clean run of the default fixture should produce. The integration test `tests/integration/test_offline_demo.py` asserts them.

## Phase outputs

| Metric | Value |
|---|---|
| Accounts discovered | 11 |
| Qualified | 9 |
| Not qualified | 1 (acc_002 - agency) |
| Insufficient evidence | 1 (acc_005 - missing employees) |
| Reconciliation: already in CRM | 1 (acc_003 - axiom.saas.example) |
| Reconciliation: ambiguous in-batch | 1 (acc_004_dup) |
| Approvable entries | 7 |
| Enrichment calls | 5 |
| Enrichment spend | 50¢ (= budget cap) |
| Entries blocked by policy | 0 |

## After approval + sync

| Metric | Value |
|---|---|
| CRM accounts total | 8 (1 seeded + 7 new) |
| create_account outcomes "ok" | 7 |
| create_contact outcomes "ok" | 5 (brightpath x2, nimbus, silentwave, incomplete with email=None) |
| idempotent_noop outcomes | 2 (skipped entries) |
| failed outcomes | 0 |

## After a second `gtm-lab sync` (idempotency)

| Metric | Value |
|---|---|
| CRM accounts total | 8 (unchanged) |
| Duplicate domains | 0 |
| `evaluate_run.no_duplicate_account_writes` | passes |

## `gtm-lab eval` output

All 4 integrity checks pass:

- `every_account_has_decision`
- `budget_respected`
- `no_policy_failed_entry_synced`
- `no_duplicate_account_writes`

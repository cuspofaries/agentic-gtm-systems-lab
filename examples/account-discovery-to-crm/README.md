# Example: Account discovery → CRM sync

End-to-end walkthrough. Takes ~1 minute to run from a clean checkout.

## What it does

1. Loads the GTM context from `config/gtm-context.yaml` - ICP, personas, policies.
2. Reads candidate accounts from `fixtures/discovery.yaml`.
3. Qualifies each account with the deterministic rules qualifier.
4. Seeds the mock CRM with `fixtures/crm-seed.yaml` so one account is "already present".
5. Reconciles, enriches (budget-enforced), and produces a WritePlan.
6. **Stops** at the human approval gate. Writes `review_<run_id>.md` to `./.gtm-lab-state/`.
7. On `approve`, syncs to the mock CRM idempotently. Re-running `sync` produces zero duplicates.

## Run it

```bash
pip install -e ".[dev]"
python -m gtm_lab.cli demo --offline
```

Then approve or reject:
```bash
gtm-lab runs                           # list run ids
gtm-lab inspect <run-id>               # JSON dump of the run
gtm-lab approve <run-id>               # promote the plan to APPROVED
gtm-lab sync <run-id>                  # apply to the mock CRM
gtm-lab eval <run-id>                  # run integrity checks
```

## Expected results

See `expected-results/summary.md` for the exact metrics any run of this fixture
should produce. The integration test `tests/integration/test_offline_demo.py` asserts them.

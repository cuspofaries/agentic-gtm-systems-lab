# End-to-end test on real data

The unit and integration tests run on fakes. This test runs the whole planning workflow on real companies and real contacts, and checks the result against what you expect: provider credits, accounts already in the CRM, contacts found, emails verified and the CRM records the plan proposes.

The code is public; the data is not. Everything the test reads or writes lives in a directory you keep out of the repository.

## The case directory

```
your-case/
  config/gtm-context.yaml   ICP, personas, budget (same format as the example)
  expected.yaml             run settings, then the expected results
  cassette.json             recorded provider exchanges (written by record mode)
  observed.yaml             what the last run produced (written by both modes)
```

`expected.yaml` starts with the run settings only:

```yaml
run:
  limit: 4                  # companies requested from People Data Labs
  profiles_per_account: 1   # people searched, emails found and checked per account
  title_levels: [cxo, vp, director]
  email_finder_cents: 10
  email_verifier_cents: 2
  signals: exa              # optional: also record Exa signal searches (needs EXA_API_KEY)
```

## Two modes

**Record** calls People Data Labs, FullEnrich, ZeroBounce and HubSpot for real, so it spends credits. The most it can spend is set by `limit`, `profiles_per_account` and `policy.enrichment_budget_cents`.

```bash
export PDL_API_KEY=... FULLENRICH_API_KEY=... ZEROBOUNCE_API_KEY=... HUBSPOT_SERVICE_KEY=...
GTM_LAB_REAL_CASE_DIR=path/to/your-case GTM_LAB_REAL_CASE_MODE=record pytest -m real_case
```

The first recording fails on purpose with "no `expect:` section yet". Read `observed.yaml`, check each value against the providers' dashboards and your CRM, and copy the values you agree with into an `expect:` section of `expected.yaml`. Keep a value out of `expect:` if you are not sure of it.

**Replay** is the default. It answers every provider call from `cassette.json`, needs no key, spends nothing and works offline:

```bash
GTM_LAB_REAL_CASE_DIR=path/to/your-case pytest -m real_case
```

It fails when a result differs from `expect:`, and it prints each difference. It also fails when the code asks a provider something the recording does not hold, or no longer makes a call that was recorded: the code changed what it asks, so record again.

## What is checked

| Key in `observed.yaml` | Meaning |
|---|---|
| `credits` | Credits each provider reported spending: PDL companies, PDL people, FullEnrich, ZeroBounce. Read from the providers' own answers, not estimated. |
| `enrichment_spent_cents` | What the workflow charged to the enrichment budget. |
| `accounts_discovered` | Companies returned by discovery. |
| `skip_existing` | Domains already in the CRM, so not created again. |
| `create_account` | Domains the plan proposes to create. |
| `approvable` | Domains with no policy failure, ready for approval. |
| `enrichment_status` | Per domain: `done`, `budget_exceeded`, `provider_failed` (for example FullEnrich out of credits) or `not_needed`. |
| `contacts_proposed`, `emails_found` | Contacts in the plan, and how many have an email. |
| `emails_by_status` | Emails by verdict: `valid`, `risky`, `invalid`, `unknown`. |
| `signals_by_kind`, `signals_spent_millidollars` | With `run.signals: exa`: signals kept per kind, and what the signal budget was charged. `credits` then also has `exa_millidollars`, read from Exa's answers. |
| `crm_writes_during_plan` | Must be empty. The test fails on any CRM call that changes data. |

The test only plans. It never approves or syncs, and HubSpot is only read.

## What the cassette stores

Provider, method, URL, request body, response status and body, and the response headers the connectors read (PDL credit headers, `Retry-After`). Request headers are never stored, since they carry the keys; ZeroBounce's `api_key` form field is removed from the body. Before writing the file the test checks that no key appears in it, and refuses to save if one does.

The responses contain real names and emails. Treat the cassette as personal data and keep it private.

## Measured on real data

Two private cases were recorded on 2026-11-22, against a HubSpot test portal that already held the companies of earlier runs.

- **Companies already in the CRM.** A search whose 9 matching companies had all been synced before: 9 found in HubSpot, 9 skipped, no contact searched, nothing written. Cost: PDL company credits only.
- **New companies.** A search for smaller companies (38 matching, 4 requested): 4 accounts to create, 2 enriched before the budget stopped the other 2 (`budget_exceeded`). One contact got an email that FullEnrich rated `HIGH_PROBABILITY` and ZeroBounce `valid`. For the other, FullEnrich ran out of credits (`provider_failed`) and the contact stayed in the plan without an email. Credits reported in the providers' answers matched their balances: FullEnrich 1, ZeroBounce 1, PDL 4 companies and 2 people. Budget charged: 68 cents (2 x 28 + 10 + 2).
- **Check the total before asking for more.** PDL's answer carries `total` (matching companies). Raising `limit` above it returns nothing new and still costs one credit per record; read it, and the CRM's existing records, before re-recording.

## Limits

- A replay runs as of the recording date (`recorded_at` in the cassette): the signal search window and signal freshness are computed from it, so a replay gives the same result months later.
- A replay shows the providers' answers from the day of recording. It catches changes in this code, not changes in the providers' data.
- FullEnrich does not charge again for a contact enriched in the last 3 months, so recording the same contacts twice in that window shows 0 FullEnrich credits.
- The CI runs the same harness against fakes (`tests/integration/test_real_case_harness.py`), so the recording, replay and comparison logic is tested on every push.

# HubSpot connector

`gtm-lab demo --crm hubspot` reconciles the plan against a real HubSpot portal, and `gtm-lab sync` then writes approved companies and contacts to it. The default stays `--crm mock`. Everything else (qualification, budget, policies, approval) is unchanged.

**Use a test portal.** Create a free HubSpot account for this, not your production CRM. The connector creates a custom property on companies and contacts (see below), and that change cannot be fully undone.

## Setup

1. In the test portal: **Settings → Integrations → Service Keys → Create** (some portals show it under *Development*). Since October 2026 HubSpot no longer lets you create private apps; Service Keys replace them.
2. Scopes: read and write on **companies** and **contacts**, and write on the **companies and contacts schema** (the connector creates one property).
3. Export the key. The CLI does not read `.env` files.
   ```bash
   export HUBSPOT_SERVICE_KEY=...   # HUBSPOT_ACCESS_TOKEN also works
   ```
4. If the portal has GDPR features on, contacts need a legal basis. Set it in `gtm-context.yaml` (see [Legal basis](#legal-basis)).
5. Recommended for the synthetic demo: turn off **"Create and associate companies with contacts"** (Settings → Objects → Companies). See [Known HubSpot behaviors](#known-hubspot-behaviors).

## Run

```bash
gtm-lab demo --crm hubspot          # plan: reads HubSpot, writes nothing
gtm-lab approve <run-id>            # writes nothing
gtm-lab sync <run-id>               # writes to HubSpot
gtm-lab sync <run-id>               # second run: no-ops only
```

`sync` uses the CRM recorded on the plan. A plan made against the mock can never be synced to HubSpot, and the reverse is also true.

## What the connector does

| Step | HubSpot calls |
|---|---|
| Plan | Search companies by domain. Read-only, no schema change. |
| First write | Create the `gtm_lab_external_ref` property on companies and contacts if missing, with `hasUniqueValue: true`. |
| Sync, per account | Look up by `gtm_lab_external_ref` (already written → no-op), search by domain (owned by someone else → `stale_plan`), then create the company. |
| Sync, per contact | Look up by ref, then create the contact with `hs_legal_basis` if configured and an association to its company (type 279). |

All calls use HubSpot's dated API version `2026-09`, through the Python standard library (no new dependency). A 429 or 5xx is retried by the sync loop; any other 4xx fails that write with HubSpot's message. The token never appears in error messages.

## Idempotency

The `gtm_lab_external_ref` property is unique in HubSpot: a second record with the same value is rejected (400 `VALIDATION_ERROR`). Every write carries a ref derived from the run and the account, so a retried or concurrent sync cannot create the same record twice, even if a lookup misses.

## Legal basis

Portals with GDPR enabled reject contacts without `hs_legal_basis`. The value is a compliance decision, so there is no default:

```yaml
crm:
  hubspot_contact_legal_basis: "Not applicable"
```

The example uses `Not applicable` because its contacts are fictional people. A test portal offered six options: legitimate interest (prospect/lead), legitimate interest (existing customer), legitimate interest (other), performance of a contract, freely given consent from contact, and not applicable. Copy the exact internal value from your portal, from the `hs_legal_basis` property settings or `GET /crm/properties/2026-09/contacts/hs_legal_basis`: some values contain a dash character that is easy to mistype. Without a value, contact writes fail with a message that points to this setting.

## Known HubSpot behaviors

Measured on a test portal, not assumed:

- **Domains are not unique in HubSpot.** It accepts two companies with the same domain. The connector searches before every create and refuses a domain that another record owns.
- **Search lags behind writes**, by about 6.5 seconds in tests, including counts after deletions. A company created by someone else in that window can be missed by the domain check. The unique ref still prevents this workflow from duplicating its own writes. For reliable counts, use the list API, not search.
- **Automatic company association by email domain.** With "Create and associate companies with contacts" on, HubSpot creates a company for each contact's email domain and makes it the contact's **primary** company. This overrides the association the connector sets, even when the connector marks it as primary. With real data the email domain matches the company, so it lines up. With the synthetic fixture (emails on `example.com`), it creates a company "example.com" and attaches every contact to it. Turn the setting off on a test portal.
- **Reserved email domains** `.invalid` and `.example` are rejected as invalid emails. The fixture uses `example.com` (reserved by RFC 2606).
- **A missing ref lookup** returns 404 with an HTML body; the connector treats it as "not found".

## Testing

- `tests/unit/test_hubspot_adapter.py` runs against an in-memory fake that reproduces the behaviors above. It runs in CI.
- `tests/live/test_hubspot_live.py` runs against a real portal and is skipped unless you opt in:
  ```bash
  export HUBSPOT_SERVICE_KEY=...      # test portal only
  GTM_LAB_LIVE_HUBSPOT=1 pytest -m live_hubspot
  ```
  It plans, approves and syncs the fixture, creates a conflicting company after approval to check `stale_plan`, re-syncs to check no-ops, and then deletes everything it created.

## Limits

- Companies and contacts only. No deals, owners or custom objects.
- Discovery and enrichment are still the synthetic fixtures. Only the CRM side is real.
- The search lag window above is a real race against other writers.

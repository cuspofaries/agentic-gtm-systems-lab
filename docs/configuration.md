# Configuration reference

Every value you can change, where it lives, what the demo uses, and what it actually does.

The demo values were chosen to exercise the 13 test scenarios on a small synthetic fixture. **They are not recommendations and none of the prices are real provider prices.** Set your own before pointing this at real data.

There are four places to configure things:

1. [The GTM context file](#1-gtm-context-file) (`gtm-context.yaml`): business rules and policies.
2. [CLI options](#2-cli-options): per-run settings.
3. [Environment variables](#3-environment-variables): state location and Anthropic credentials.
4. [Code constants](#4-code-constants): values that are not exposed yet and need a code change.

The fixture files (discovery, enrichment, CRM seed) are covered in [section 5](#5-fixture-files).

---

## 1. GTM context file

Path in the example: `examples/account-discovery-to-crm/config/gtm-context.yaml`. Loaded and validated by `gtm_lab.context.load_context`; an unknown key is an error.

### Top level

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `version` | string | `"1.0.0"` | Informational. Not read by the workflow. Use it to track changes to your context. |
| `company_positioning` | string | short text | Informational. Not read by the workflow or sent to the LLM. |

### `icp`

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `employees_min` | int | `20` | Lower bound, inclusive. Fewer employees → `not_qualified`. |
| `employees_max` | int | `200` | Upper bound, inclusive. More employees → `not_qualified`. |
| `industry_keywords` | list of strings | `saas`, `software`, `technology` | The account's industry label must contain at least one (case-insensitive substring). An empty industry → `insufficient_evidence`. An empty list disables the industry check. |
| `geographies` | list of strings | `US` | Allowed `hq_country` values (compared case-insensitively). A missing country → `insufficient_evidence`. An empty list disables the check. |
| `sales_motion` | string | `sales-assisted B2B` | Not compared by the rules. Sent to the LLM as part of the ICP in agentic mode. |

A missing employee count gives `insufficient_evidence`, never a guess. If the headcount is present but its confidence is `approx` or `unknown`, the decision confidence is lowered by 0.15 (see [code constants](#4-code-constants)).

### `personas`

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `name` | string | `VP Sales`, `Head of Sales`, `Revenue Operations`, `Sales Enablement` | Passed to the enrichment provider as the personas to look for. The fixture provider matches these names exactly against the keys in `enrichment.yaml`. |
| `titles_any_of` | list of strings | e.g. `vp sales`, `vp of sales` | **Not used yet.** No code reads it today. It is there for a real provider adapter to map persona names to job titles. |

### `signal_policy`

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `relevant_kinds` | list of strings | `sales_hiring`, `new_sales_leadership`, `revenue_expansion` | Only signals of these kinds count. Allowed kinds are `sales_hiring`, `new_sales_leadership`, `revenue_expansion`, `other`. |
| `max_signal_age_days` | int | `90` | A signal older than this, or without an event date, does not count as evidence. |

A fresh relevant signal adds one evidence fact and +0.1 confidence. It is only required if `policy.require_signal_for_qualification` is true.

### `exclusions`

A list of rules checked before anything else. Any match → `not_qualified` with confidence 1.0.

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `name` | string | `not-for-profit`, `consulting-agencies` | Label shown in the decision evidence. |
| `reason` | string | short text | Shown in the decision evidence. |
| `industry_keywords` | list of strings | `nonprofit`, `consulting`, `agency`... | Excludes accounts whose industry contains one of these (case-insensitive). Optional. |
| `domain_suffixes` | list of strings | not used in the demo | Excludes accounts whose domain ends with one of these. Optional. |

### `policy`

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `enrichment_budget_cents` | int | `50` | Total enrichment spend allowed per run, in cents. Before each enrichment call the workflow checks that the call's cost still fits; if not, the call is skipped and the entry is marked `budget_exceeded`. The demo value is sized to run out on the 6th of 7 accounts so the "budget exceeded" scenario happens. |
| `require_signal_for_qualification` | bool | `false` | If true, an account without a fresh relevant signal is `not_qualified`, even if it matches the ICP. |
| `require_approval_before_crm_write` | always `true` | `true` | Typed as `Literal[True]`: setting it to `false` fails validation. The human approval gate cannot be turned off through config. |
| `min_evidence_count` | int | `2` (model default: `1`) | Minimum number of evidence facts for `qualified`. Below it the decision is `insufficient_evidence`, and the policy check blocks the entry. |
| `require_contact_email_for_create` | bool | `false` | If true, a new account is only approvable if at least one proposed contact has an email. |

### `crm`

Only used with `--crm hubspot`. See [`docs/hubspot.md`](hubspot.md).

| Key | Type | Demo value | Effect |
|---|---|---|---|
| `hubspot_contact_legal_basis` | string or null | `"Not applicable"` | Sent as `hs_legal_basis` on every HubSpot contact. Required by portals with GDPR enabled. No default: it is a compliance decision. The demo value is truthful only because the fixture contacts are fictional. |

---

## 2. CLI options

`DIR` defaults below come from `GTM_LAB_STATE_DIR` (see section 3) and from the bundled example.

| Command | Option | Default | Effect |
|---|---|---|---|
| `demo` | `--offline` / `--agentic` | `--offline` | `--agentic` sends `insufficient_evidence` accounts to Claude. Falls back to offline, and says so, if no Anthropic credentials are found. |
| `demo` | `--limit` | `50` | Maximum number of accounts requested from the discovery provider. |
| `demo` | `--crm` | `mock` | CRM the plan is reconciled against: `mock` (local SQLite) or `hubspot` (needs `HUBSPOT_SERVICE_KEY`). `sync` later writes to the CRM recorded on the plan. |
| `demo`, `runs`, `eval`, `sync` | `--json` | off | Structured output instead of tables. |
| `demo` | `--max-llm-calls` | `5` | Agentic mode only. Hard cap on LLM calls per run. Past the cap, accounts stay `insufficient_evidence`. |
| `demo` | `--max-llm-cents` | `50` | Agentic mode only. Cap on estimated LLM spend per run, in cents, computed from the token usage of each response. It is checked before each call, so the call that crosses it still completes; no call starts once it is reached. |
| `demo`, `approve`, `reject`, `sync` | `--example-dir` | bundled example | Directory containing `config/gtm-context.yaml` and `fixtures/`. Point it at your own copy to use your own context and fixtures. |
| all commands | `--state-dir` | `./.gtm-lab-state` | Where run state, the mock CRM, the event log and review artifacts are written. |
| `reject` | `--reason` | empty | Free-text reason stored in the event log (first 500 characters). |
| `eval`, `inspect`, `approve`, `reject`, `sync` | `RUN_ID` (argument) | required | The run to act on. `gtm-lab runs` lists them. |

---

## 3. Environment variables

The CLI does **not** read a `.env` file. Export these in your shell, or use your own loader.

| Variable | Default | Effect |
|---|---|---|
| `GTM_LAB_STATE_DIR` | `./.gtm-lab-state` | Default for `--state-dir` on every command. |
| `ANTHROPIC_API_KEY` | unset | Credential for agentic mode. |
| `ANTHROPIC_AUTH_TOKEN` | unset | Alternative credential for agentic mode. |
| `HUBSPOT_SERVICE_KEY` | unset | HubSpot Service Key for `--crm hubspot`. `HUBSPOT_ACCESS_TOKEN` is accepted too. |
| `GTM_LAB_LIVE_HUBSPOT` | unset | Set to `1` to run the live HubSpot test (`pytest -m live_hubspot`). |

Agentic mode also accepts an `ant auth login` profile (`~/.config/anthropic/`). If none of the three is present, `--agentic` runs offline and says so.

---

## 4. Code constants

These are not exposed as config yet. Changing them means editing the code.

| Value | Where | Current | Effect |
|---|---|---|---|
| Enrichment cost per call | `providers/fixture_enrichment.py`, `cost_cents_per_call` | `10` cents | Synthetic price of the fixture provider. A real provider adapter sets its own. |
| HubSpot API version | `crm/hubspot.py`, `API_VERSION` | `2026-09` | Dated HubSpot API version used for every call. |
| HubSpot idempotency property | `crm/hubspot.py`, `REF_PROPERTY` | `gtm_lab_external_ref` | Unique custom property created on companies and contacts. |
| Sync retries | `workflows/account_discovery.py`, `sync(max_retries=2)` | `2` | Retries per CRM write on a retriable error, so 3 attempts in total. Immediate, no backoff. |
| Base confidence for `qualified` | `agents/qualifier.py` | `0.7` | Starting confidence when all ICP checks pass. |
| Fresh-signal bonus | `agents/qualifier.py` | `+0.1` | Added when at least one relevant signal is in the window. |
| Uncertain headcount penalty | `agents/qualifier.py` | `-0.15` | Applied when the headcount confidence is `approx` or `unknown`. |
| Confidence bounds | `agents/qualifier.py` | `0.3` to `0.99` | Clamp on the `qualified` confidence. |
| Confidence for `insufficient_evidence` | `agents/qualifier.py` | `0.4` (missing field), `0.5` (below evidence floor) | Fixed values; they label uncertainty, they are not probabilities. |
| LLM model | `agents/anthropic_provider.py`, `DEFAULT_MODEL` | `claude-opus-5-5` | Model used in agentic mode. |
| LLM price table | `agents/anthropic_provider.py`, `PRICE_PER_MTOK` | $4 input / $20 output per million tokens | Only used to enforce `--max-llm-cents`. Update it if prices change. |
| LLM effort | `agents/anthropic_provider.py` | `low` | Reasoning depth for the qualification call. |
| LLM max output tokens | `agents/anthropic_provider.py` | `16000` | Per-call output limit. |
| LLM output truncation | `agents/qualifier.py` | explanation 1000 chars, 20 evidence items, 20 missing items | Applied to every LLM answer before it is stored. |

---

## 5. Fixture files

Used by the offline demo. Copy them next to your own `gtm-context.yaml` and point `--example-dir` at the folder.

| File | What you can set |
|---|---|
| `fixtures/discovery.yaml` | `accounts` (id, domain, name, employees, `employees_confidence`, industry, `hq_country`, `hq_state`, `fields_missing`, `signals` with kind, description, `event_date`, confidence, source) and `source_failures` (provider errors to report). |
| `fixtures/enrichment.yaml` | `by_domain`: for each domain, the contacts returned per persona name (`first_name`, `last_name`, `title`, `email`, `verified`). `fails_for_domains`: domains for which the provider reports a failure. Use `example.com` emails (RFC 2606), never real ones. HubSpot rejects `.invalid`. |
| `fixtures/crm-seed.yaml` | Accounts loaded into the mock CRM whenever it is empty. In the demo, one account is seeded so the "already in CRM" scenario happens. |

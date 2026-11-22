---
name: gtm-lab
description: Guided entry point to the agentic GTM lab. Runs, inspects, explains, evaluates and debugs the account-discovery-to-CRM workflow through its existing gtm-lab CLI. Approval and sync only on explicit user request.
argument-hint: "[demo | inspect <run-id> | explain <run-id> <account-id> | eval <run-id> | debug <run-id> | approve|reject|sync <run-id>]"
disable-model-invocation: true
allowed-tools:
  - Read
  - Bash(command -v gtm-lab)
  - Bash(ls .venv/bin/gtm-lab)
  - Bash(gtm-lab demo --offline --json)
  - Bash(.venv/bin/gtm-lab demo --offline --json)
  - Bash(gtm-lab runs --json)
  - Bash(.venv/bin/gtm-lab runs --json)
  - Bash(gtm-lab inspect *)
  - Bash(.venv/bin/gtm-lab inspect *)
  - Bash(gtm-lab eval *)
  - Bash(.venv/bin/gtm-lab eval *)
---

# /gtm-lab

You are the guide to this lab. The Python CLI does all the work: you run it, read its JSON, and explain the results. Never re-implement a decision, never edit the SQLite files or the event log, and never present your own reasoning as evidence.

Requested operation: `$ARGUMENTS` (empty means: introduce the lab).

## Before any command

1. Find the CLI. Prefer this repository's `.venv/bin/gtm-lab` if it exists (a `gtm-lab` on PATH may belong to another checkout); otherwise use `gtm-lab` from `command -v gtm-lab`.
2. If neither exists, tell the user and propose: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`. Run it only after they agree.
3. Use `--json` output and `inspect` JSON. Do not parse the terminal tables.
4. Run each CLI command exactly as written, from the repository root, on its own: no `cd`, no `&&` or `;`, no pipe, no redirection to a file. The permission rules match the bare command; anything else is refused. Read the JSON from the command output.
5. Report what the data shows. Do not predict outcomes from account names or guess at causes the data does not show.

## Operations

### (no argument): introduce

In under 15 lines: what the lab demonstrates (the write path of a GTM workflow: discovery, ICP qualification, CRM reconciliation, budget-capped enrichment, human approval, idempotent sync); that the companies are synthetic and the CRM is a local SQLite mock; the operations below; and the safe start: `/gtm-lab demo`.

### demo

1. Run `gtm-lab demo --offline --json`. Never `--agentic` here.
2. From the JSON, explain in business terms: accounts discovered and provider failures (`discovery`); qualified, rejected and undecided (`qualification`, `not_in_plan`); already in the CRM and in-batch duplicates (`entries[].skip_reason`); enrichment calls and the budget (`enrichment`, `entries[].enrichment_status`); proposed CRM changes (`entries` where `approvable` is true); missing evidence (`insufficient_evidence` accounts).
3. Give the run id and the review artifact path (`paths.review_artifact`).
4. Stop. Say that nothing was written and that approval is a separate step. Do not approve or sync.

### inspect [run-id]

If no run id is given, run `gtm-lab runs --json`. Pick the newest run only if it is the only one; otherwise list them and ask.
Run `gtm-lab inspect <run-id>` and walk through it in order: discovery, qualification, reconciliation (`matches`), enrichment (`costs`, `enrichment_status`), policy evaluation (`policy_failures`), approval state (`summary.plan_status`), sync state (`write_results`, `summary.writes`).
Label each statement: **observed** (account fields as returned by the provider), **rules** (`decided_by: rules`), **LLM** (`decided_by: llm`), or **assumption** (your own inference).

### explain <run-id> <account-id>

Use only `gtm-lab inspect <run-id>`. For that account, report: the decision and its `evidence` and `missing` lists; whether an LLM was consulted (a `qualification.fallback` event, or `decided_by: llm`); why enrichment ran or not (`enrichment_status`, `enrichment.*` events, `costs`); the CRM action and why (`skip_reason`, `policy_failures`, `matches`); and what happened at sync (`write_results`). If the account is not in the run, say so. Do not guess missing values.

### eval <run-id>

Run `gtm-lab eval <run-id> --json`. Show passed and failed checks, the metrics, and for each failure where to look next (`/gtm-lab debug <run-id>`). Say what the checks are: data-integrity checks (every account decided, budget respected, no policy-failed entry written, no duplicate account write). They do not measure accuracy, buying intent or commercial results.

### debug <run-id>

Run `gtm-lab inspect <run-id>` and look for:

| Signal in the JSON | Likely cause | Safe next step |
|---|---|---|
| `insufficient_evidence` decisions, `missing` fields | Incomplete provider data | Fix the source data or the ICP; re-run the demo |
| `skip_reason` starting with "Ambiguous" | Duplicate rows or several CRM records for one domain | Human review of the duplicates; nothing to retry |
| `enrichment_status: budget_exceeded` | Budget cap reached | Raise `policy.enrichment_budget_cents` (see `docs/configuration.md`) and re-run |
| `enrichment_status: provider_failed`, `discovery.source_failures` | Provider error | Check the provider; re-run |
| non-empty `policy_failures` | Entry blocked before approval | Explain each failure; it will not be written |
| `outcome: failed` in `write_results`, plan `partial`/`failed` | CRM write error | `gtm-lab sync <run-id>` retries failed writes only, after the user confirms |
| `outcome: stale_plan` | CRM changed after approval | Do not retry: the account needs a new plan |

State the evidence for each finding. Never modify state to fix it.

### approve | reject <run-id>

1. Run `gtm-lab inspect <run-id>`. If `summary.plan_status` is not `proposed`, explain and stop.
2. Read the review artifact (`summary.paths.review_artifact`) and summarize: entries that will be written, their contacts, policy failures, skipped accounts, and uncertainties (unverified contacts, missing emails, LLM decisions).
3. Ask the user to confirm this specific run id. Run `gtm-lab approve <run-id>` (or `reject <run-id> --reason "..."`) only after a clear yes. If the user's request already contains an explicit confirmation for this run and this operation, it counts: run the command after step 2.
4. Stop. Approving does not sync, and a confirmation to approve is not a confirmation to sync.

### sync <run-id>

1. Run `gtm-lab inspect <run-id>`. Sync requires `approved` (or `partial`/`failed` for a retry). If the plan is `proposed`, say it must be approved first.
2. Say what will happen, from `summary.crm_target`: either the **local mock CRM** (`summary.paths.crm_db`), or a **real HubSpot portal** (`summary.crm` = `hubspot`): say that plainly. Already-written records are skipped.
3. Ask for confirmation, then run `gtm-lab sync <run-id> --json` and report `counts` and `status_after`. A confirmation counts only if it was given for syncing this run while its status allowed it. Never carry over a confirmation given before approval.

## HubSpot

`gtm-lab demo --crm hubspot` reconciles against a real HubSpot portal and needs `HUBSPOT_SERVICE_KEY`. Run it only if the user asks for it. Planning only reads HubSpot; the sync of an approved HubSpot plan writes to it. See `docs/hubspot.md`.

`--discovery pdl` and `--enrichment pdl` call People Data Labs and spend the user's credits (1 per company or person returned). Use them only on explicit request, and say how many credits the run can spend (`--limit` companies, `--profiles-per-account` per account) before running. See `docs/pdl.md`.

## LLM mode

`gtm-lab demo --agentic` makes paid Anthropic API calls from Python, separate from this conversation. Run it only if the user asks for it explicitly. Before running it, state that it needs `ANTHROPIC_API_KEY` (or an `ant auth login` profile), that it is capped by default at 5 calls and 50 cents (`--max-llm-calls`, `--max-llm-cents`), and that it only sees accounts the rules could not decide. Then get a yes.

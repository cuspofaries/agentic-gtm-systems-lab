# agentic-gtm-systems-lab

A reference implementation of one GTM workflow: account discovery → ICP qualification → CRM reconciliation → budget-capped enrichment → human approval → sync. By default the data is synthetic and the CRM is a local SQLite mock; real providers (People Data Labs, FullEnrich, ZeroBounce, HubSpot) are opt-in flags. It is a lab, not a production pipeline.

## Where things are

- `src/gtm_lab/`: the implementation. `workflows/account_discovery.py` is the orchestrator; `agents/` holds the rules qualifier and the optional LLM qualifier; `crm/mock.py` is the mock CRM; `policies/rules.py` the pre-approval checks; `evaluations/checks.py` the four post-run integrity checks.
- `examples/account-discovery-to-crm/config/gtm-context.yaml`: ICP, personas, signal policy, exclusions, budget. Every key is documented in `docs/configuration.md`.
- `examples/account-discovery-to-crm/fixtures/`: synthetic discovery, enrichment and CRM seed data.
- `.gtm-lab-state/` (or `$GTM_LAB_STATE_DIR`): run state (`runs.sqlite`), mock CRM (`crm.sqlite`), audit log (`events.jsonl`), review artifacts (`review_<run-id>.md`). Git-ignored.

## CLI

Installed by `pip install -e ".[dev]"` into `.venv` (use `.venv/bin/gtm-lab` if `gtm-lab` is not on PATH).

| Command | Effect |
|---|---|
| `gtm-lab demo --offline [--json]` | Plan a run, write the review artifact, stop at approval. No CRM write, no LLM. |
| `gtm-lab demo --crm hubspot` | Same, reconciled against a real HubSpot portal (reads only). Only on explicit request. |
| `gtm-lab demo --discovery pdl --enrichment pdl` | Real companies and contacts from People Data Labs; spends credits. Only on explicit request. |
| `gtm-lab demo ... --email-finder fullenrich --email-verifier zerobounce` | Finds and checks contact emails; spends credits. Only on explicit request. |
| `gtm-lab runs [--json]` | List runs, newest first. |
| `gtm-lab inspect <run-id>` | All persisted state of a run as JSON: `summary`, accounts, decisions, matches, costs, plan, write results, audit events. |
| `gtm-lab eval <run-id> [--json]` | Four integrity checks. Exit code 1 on failure. |
| `gtm-lab approve <run-id>` | `proposed` → `approved`. Writes nothing. |
| `gtm-lab reject <run-id> [--reason ...]` | `proposed` → `rejected`. |
| `gtm-lab crm-setup --crm hubspot` | Prepares a HubSpot portal for writes (creates a unique custom property). State-changing, asks for confirmation. Only on explicit request. |
| `gtm-lab sync <run-id> [--json]` | Write an approved plan to the CRM it was planned against (mock or HubSpot). Re-running is a no-op. |

Read `--json` output or `inspect` instead of parsing the terminal tables.

## Offline vs LLM mode

- Offline (`--offline`, the default): deterministic rules only, no network, no key.
- Agentic (`--agentic`): accounts the rules mark `insufficient_evidence` are sent to the Anthropic API from Python, with call and spend caps. This is a paid API call made by the application. It is separate from Claude Code's own reasoning. Never run it unless the user explicitly asks for it and confirms the cost.

## Safety rules

- Never approve, reject or sync a run unless the user explicitly asks for that specific run in the current turn. A finished demo is not approval.
- Approve and sync are separate steps: never run both from one request.
- Before approving, show the user the review artifact's content: approvable entries, policy failures, skipped accounts, uncertain data.
- Never edit `crm.sqlite`, `runs.sqlite` or `events.jsonl` by hand to fix a run. Use the CLI.
- A plan with `summary.crm` = `hubspot` writes to a real HubSpot portal on sync. Say so explicitly before asking for confirmation. Never pick `--crm hubspot` unless the user asks for it.
- Explanations must come from persisted data (`inspect`). Label anything inferred as an assumption.

## Tests

```bash
ruff check .
pytest -q
```

## Claude Code skill

`/gtm-lab` (in `.claude/skills/gtm-lab/`) is the guided entry point: `/gtm-lab`, `/gtm-lab demo`, `/gtm-lab inspect <run-id>`, `/gtm-lab explain <run-id> <account-id>`, `/gtm-lab eval <run-id>`, `/gtm-lab debug <run-id>`, `/gtm-lab approve|reject|sync <run-id>`. See `docs/claude-code.md`.

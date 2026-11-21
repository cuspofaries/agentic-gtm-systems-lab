# Claude Code evolution report

*2026-11-21 · branch `feature/claude-code-skill` · cuspofaries*

## Baseline (before any change, `main` @ `954e06c`)

- `ruff check .`: clean.
- `pytest -q`: 47 passed (31 unit, 3 integration, 13 scenarios).
- `gtm-lab demo --offline` → `approve` → `sync` → `sync` again → `eval`: proposed run, 12 ok + 2 no-op on first sync, 14 no-op on the second, 4/4 checks passed.
- No Claude Code integration: no `CLAUDE.md`, no skill. The CLI printed tables only (`inspect` was the one JSON output); illegal transitions surfaced as Python tracebacks.

## 1. Files created and modified

Created:
- `CLAUDE.md`: orientation, CLI table, offline vs LLM mode, CRM safety rules.
- `.claude/skills/gtm-lab/SKILL.md`: the `/gtm-lab` skill (91 lines).
- `src/gtm_lab/summary.py`: run summary and detail built only from persisted state.
- `tests/integration/test_claude_code_integration.py`: 12 tests.
- `docs/claude-code.md`, this report.

Modified:
- `src/gtm_lab/cli.py`: `--json` on `demo`, `runs`, `eval`, `sync`; richer `inspect`; clear errors; explicit state transitions.
- `src/gtm_lab/persistence/store.py`: `load_matches`, `load_costs`.
- `src/gtm_lab/workflows/account_discovery.py`: the `workflow.start` event now records whether an LLM fallback is configured (one line).
- `README.md` (two entry points, layout, capabilities), `IMPLEMENTATION_REPORT.md` (pointer to this report).

## 2. Architecture changes

None to the workflow. The skill is a thin layer over the existing CLI: it runs commands, reads JSON, and explains. Decisions, budget, policies, the approval state machine and sync stay in Python. No new framework, service, agent loop or nested LLM call in the application.

## 3. Existing features preserved

All 47 baseline tests pass unchanged. Default (non-`--json`) output of `demo`, `runs`, `eval`, `approve`, `reject` and `sync` keeps its shape; `runs` still prints bare run ids (CI depends on it). `inspect` keeps its historical keys (`run_id`, `accounts`, `decisions`, `plan`, `write_results`) and adds `summary`, `matches`, `costs`, `events`.

Behavior changes, all from failure to clearer failure:
- Unknown run id on `inspect`, `eval`, `approve`, `reject`, `sync`: clean message, exit code 1. Before: a traceback or an empty JSON document with exit code 0 for `inspect`.
- `sync` before approval, approving twice, syncing or approving a rejected run: clean message, exit code 1. Before: a traceback. The state machine itself is unchanged.

## 4. Skill capabilities

`/gtm-lab` with no argument, `demo`, `inspect [run-id]`, `explain <run-id> <account-id>`, `eval <run-id>`, `debug <run-id>`, `approve | reject <run-id>`, `sync <run-id>`.

Frontmatter: `disable-model-invocation: true` (user-invoked only), `argument-hint`, `$ARGUMENTS`, and `allowed-tools` pre-authorizing only read-only commands. `demo` is pre-authorized as an exact command (`gtm-lab demo --offline --json`) so a wildcard cannot let `--agentic` through.

## 5. Commands tested

CLI (manual and automated): `demo --offline [--json]`, `runs [--json]`, `inspect`, `eval [--json]`, `approve`, `reject`, `sync [--json]`, plus every error path above.

Claude Code 2.1.330, `claude -p` (print mode) with `claude-opus-5-5`, on a fresh clone with its own `.venv`. Each session was isolated: `--setting-sources project`, `--strict-mcp-config` (no MCP servers loaded), `--no-session-persistence`, `--permission-prompts none`.

| Invocation | What Claude Code did | Resulting state |
|---|---|---|
| `/gtm-lab` | Introduction, no tool call | none |
| `/gtm-lab demo` | Located `.venv/bin/gtm-lab`, ran `demo --offline --json`, explained, stopped | 1 run `proposed`, CRM: seed only |
| `/gtm-lab sync <run>` (not approved) | Inspected, refused, did not call sync | unchanged |
| `/gtm-lab approve <run>` | Inspected, read the review artifact, listed uncertainties, asked for confirmation | unchanged |
| `/gtm-lab approve <run>` + confirmation in the prompt, no tool permission | Called `approve`; Claude Code denied it | unchanged |
| same, with `--allowedTools "Bash(.venv/bin/gtm-lab approve *)"` | Approved, stopped, said sync needs its own confirmation | `approved`, CRM: seed only |
| `/gtm-lab sync <run>` + confirmation, sync permission | Inspected, ran `sync --json`, reported counts | `synced`, CRM: 8 accounts |
| `/gtm-lab explain <run> acc_005` | Explained from `inspect`, labeled rules/observed | none |
| `/gtm-lab eval <run>` | Ran `eval --json`, stated what checks do not measure | none |
| `/gtm-lab debug <run>` | Five findings with evidence and safe next steps, no mutation | none |

## 6. Test results

- `ruff check .`: clean.
- `pytest -q`: 59 passed (31 unit, 15 integration, 13 scenarios). The 12 new tests check the skill's location and frontmatter; that it and `CLAUDE.md` only name real commands and flags; that it pre-authorizes no state change and no `--agentic`; that `demo --json` builds no LLM provider and writes nothing; that `inspect` matches persisted data; that sync is refused without approval and after rejection; that unknown runs fail cleanly; the full approve-to-sync lifecycle through the CLI including idempotency; and that the default `runs` output is unchanged.
- Fresh clone: `pip install -e ".[dev]"` and the 59 tests pass.

## 7. Security and cost controls

- The skill cannot be auto-invoked by Claude.
- State-changing commands and `--agentic` are never pre-authorized; Claude Code asks every time. The live tests showed this holds even when the user's confirmation is written in the prompt.
- The CLI enforces the approval state machine regardless of what calls it.
- `/gtm-lab demo` is offline by construction; the paid LLM path needs an explicit request, a confirmation and a permission prompt, and keeps its call and spend caps.
- The skill forbids editing the SQLite files and the event log.
- Measured cost of one skill operation in the tests: $0.04 to $0.26 as reported by Claude Code, $2.09 in total for the 12 test sessions. The offline workflow makes no API call.
- The test sessions loaded no MCP server and no user plugin, so no Telegram or other channel was involved.

## 8. Known limitations

- The interactive terminal UI was not exercised. In print mode a permission prompt cannot be answered; granting permission was simulated with `--allowedTools`.
- `demo --agentic` was not run with a real key.
- Pre-authorized commands must be run bare. In one live run Claude prefixed `cd ...;` and redirected output to a file; those commands did not match the rules and were refused. The skill now requires bare commands; after that change the same scenario succeeded.
- Claude's explanations are summaries, not evidence. In one live run it wrote "5 accounts" and listed 6; in another, before the skill was tightened, it guessed from an account name that a write would fail. The JSON is the source of truth.
- `ReconciliationMatch.duplicates_in_crm` holds the id of the other batch row for an in-batch duplicate, not a CRM id. Found by `/gtm-lab debug`; documented, not renamed, to keep the data contract stable.
- `inspect` output is large (every record and audit event); Claude Code stores it as a tool result file and reads it from there.

## 9. Interactive Claude Code test results

See section 5. All runs used real Claude Code in print mode. No test used a mocked Claude.

## 10. Recommended next improvements

1. Test once in the interactive UI, answering the permission prompts by hand.
2. Rename or alias `duplicates_in_crm` to something like `duplicate_of` in a versioned change of the data contract.
3. Add `--summary` to `inspect` to return only the summary block when the full record set is not needed.
4. A `gtm-lab replan <run-id>` for stale entries, which `/gtm-lab debug` could then propose.

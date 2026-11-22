# Using the lab from Claude Code

The repository ships a project skill, `/gtm-lab`, that lets you explore and drive the workflow by conversation. Claude Code reads the CLI's JSON output and explains it; the Python CLI does all the work. Nothing in the skill re-implements a decision.

- **Claude Code** is the interactive guide: it runs commands, reads persisted state, explains decisions, and asks before anything changes state.
- **Python** stays the execution layer: qualification, budget, policies, approval state machine and idempotent sync are all in `src/gtm_lab/`, tested without Claude Code.

## Install and discover

```bash
git clone https://github.com/cuspofaries/agentic-gtm-systems-lab.git
cd agentic-gtm-systems-lab
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # the skill can propose this for you
claude
```

Then type `/gtm-lab`. The skill lives in `.claude/skills/gtm-lab/SKILL.md` and is loaded automatically from the project. It is **user-invoked only** (`disable-model-invocation: true`): Claude never starts it on its own, and it is not listed among the skills Claude can pick.

## Commands

| You type | What happens | Changes state? |
|---|---|---|
| `/gtm-lab` | Short introduction and the safe first step | No |
| `/gtm-lab demo` | Runs `gtm-lab demo --offline --json`, explains the result, stops at approval | Creates a proposed run. No CRM write, no LLM |
| `/gtm-lab inspect [run-id]` | Walks through a run step by step from `gtm-lab inspect` | No |
| `/gtm-lab explain <run-id> <account-id>` | Why one account was qualified, enriched, skipped or written | No |
| `/gtm-lab opportunity <run-id>` | Reads the run's Account Opportunity Brief: evidence, hypotheses, roles, next action, baseline next to the agent | No |
| `/gtm-lab eval <run-id>` | Runs the four integrity checks and explains them | No |
| `/gtm-lab debug <run-id>` | Finds problems in the persisted state and proposes a safe next step | No |
| `/gtm-lab approve <run-id>` | Summarizes the review artifact, asks you to confirm, approves | Plan `proposed` → `approved`. No CRM write |
| `/gtm-lab reject <run-id>` | Same, but rejects | Plan `proposed` → `rejected` |
| `/gtm-lab sync <run-id>` | Checks the plan is approved, asks you to confirm, writes | Writes to the **local mock CRM** only |

## How the safety works

Two layers, and only the second is a hard guarantee.

1. **Skill instructions.** Never approve or sync after a demo, approve and sync are separate requests, a confirmation covers only the operation and run it was given for, explanations come from persisted data and inferences are labeled as assumptions.
2. **Permissions and code.**
   - The skill's `allowed-tools` pre-authorizes only read-only commands: `demo --offline --json` (exact match, so `--agentic` cannot slip in), `runs --json`, `inspect`, `eval`, and locating the CLI. `approve`, `reject`, `sync` and `demo --agentic` always go through Claude Code's permission prompt.
   - The CLI refuses illegal transitions on its own: `sync` before approval, approving twice, syncing or approving a rejected run all exit with code 1 and change nothing.
   - `require_approval_before_crm_write` is typed `Literal[True]` in the config model.

Do not treat Claude's explanations as verified evidence. They summarize the JSON; the JSON is the evidence.

## Offline demo walkthrough (real output)

The excerpts below come from real `/gtm-lab` runs on Claude Code 2.1.330 with `claude-opus-5-5`, on a fresh clone. The run id is `run_20261121T221922Z`. Long answers are shortened (`[...]`); nothing was rewritten.

**`/gtm-lab demo`**: tool calls `ls .venv/bin/gtm-lab`, then `.venv/bin/gtm-lab demo --offline --json`.

> I ran the demo offline, so no LLM calls were made. It built a proposed plan of **7 CRM account creations**, and nothing has been approved or written yet.
>
> **Discovery**
> - The provider returned 11 accounts, all synthetic.
> - One source failed: `linkedin_sales_navigator` returned `429 rate_limit`. The run carried on with the accounts it got.
> [...]
>
> **Enrichment** (looking up contacts for each account)
> - The budget was 50¢ and all 50¢ was spent.
> - 5 accounts were enriched.
> - **Ninth Budget Corp** and **Failwrite LLC** got no enrichment because the cap was reached. They are still in the plan, with no contacts.
> [...]
>
> The plan is `proposed`, and all write counters are 0. To go further, use `/gtm-lab approve run_20261121T221922Z`, which shows you the plan and asks you to confirm before approving. Syncing to the local mock CRM is a separate step after that.

**`/gtm-lab sync run_20261121T221922Z`** before approval: the skill inspected the run and did not call `sync`.

> I can't sync `run_20261121T221922Z` yet because the plan hasn't been approved. Its status is **`proposed`**, and sync only runs on plans that are `approved`, or `partial`/`failed` when retrying. I didn't run sync, and nothing has been written to the CRM [...]

**`/gtm-lab approve run_20261121T221922Z`**: the skill read the review artifact, listed what would be written and the uncertainties (2 unverified contacts, one without email, budget cap reached, a failed discovery source), then asked:

> **Do you want me to approve `run_20261121T221922Z`?** Approving only records the decision. Nothing is written to the local mock CRM until you run a sync, which is a separate step.

**Approval confirmed, then sync confirmed separately.**

> Run `run_20261121T221922Z` is now **approved**. Nothing has been written to the CRM yet. [...] Approving doesn't sync, and your confirmation to approve doesn't cover syncing.

> The sync finished and run `run_20261121T221922Z` went from **approved** to **synced**. It wrote only to the local mock CRM (`.gtm-lab-state/crm.sqlite`).
>
> **Counts:** 12 `ok`, 2 `idempotent_noop`, 0 `failed`, 0 `stale_plan`

**`/gtm-lab explain run_20261121T221922Z acc_005`**

> **In short:** the rules could not decide on this account because the provider gave no employee count. It was left out of the CRM plan, so nothing was enriched, nothing was proposed and nothing was written. No LLM was involved.
> - **rules:** `insufficient_evidence`, confidence 0.4, `decided_by: rules`.
> - **rules, missing:** `employees unknown`. [...]

**`/gtm-lab eval run_20261121T221922Z`** ends with:

> **What these checks don't cover:** they only confirm the pipeline followed its own rules. They don't measure whether the qualification decisions were accurate, whether the accounts have buying intent, or any commercial result.

## Debugging and evaluation

`/gtm-lab debug <run-id>` reads `gtm-lab inspect` and maps what it finds to a cause and a safe next step: undecided accounts and missing fields, ambiguous matches, budget exhaustion, provider failures, policy failures, failed or partial syncs, stale plans. It never edits the SQLite files or the event log. The only recovery command it may propose is `gtm-lab sync <run-id>` for failed writes, which retries only what failed, and only after you confirm.

## Optional LLM mode (Python, paid)

Your Claude Code login is enough for everything above: the skill only runs `gtm-lab` commands that make no LLM call. The modes below are different: the Python program itself calls the Anthropic API, so it needs its own key in the environment (`ANTHROPIC_API_KEY`, exported in the shell you start `claude` from, or an `ant auth login` profile). Your Claude Code login is not passed to it.

`gtm-lab demo --agentic` is a feature of the Python app, not of Claude Code. It sends the accounts the rules could not decide to the Anthropic API for an opinion that is recorded and never changes the decision, under hard caps (default 5 calls, 50 cents; `--max-llm-calls`, `--max-llm-cents`) and needs `ANTHROPIC_API_KEY` or an `ant auth login` profile. Those calls are billed to that key, separately from your Claude Code session. The same applies to `--research exa` and `--opportunity exa` (which also need `EXA_API_KEY`) and to `gtm-lab opportunity-eval --record` / `--extend`.

The skill never runs it from `/gtm-lab demo`. It runs it only if you ask for it explicitly and confirm the cost, and the command still goes through a permission prompt.

## Costs

Using the skill consumes Claude Code usage like any conversation. In the tests above, one `/gtm-lab` operation took 1 to 6 turns, with a cost reported by Claude Code of $0.04 to $0.26 each. The offline workflow itself makes no API call.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/gtm-lab` is not listed | Claude Code was started outside the repository | Start `claude` from the repository root |
| The skill says the CLI is missing | No `.venv` and no `gtm-lab` on PATH | Accept the proposed install, or run `python -m venv .venv && .venv/bin/pip install -e ".[dev]"` |
| A `gtm-lab` command asks for permission | Only read-only commands are pre-authorized | Expected for `approve`, `reject`, `sync`, `--agentic`, `--research` |
| A read command is refused in a non-interactive session | Claude added `cd`, a pipe or a redirection, which no longer matches the permission rule | The skill tells it to run bare commands; it retries without them |
| `Error: No run '...'` | Wrong run id or state directory | `/gtm-lab inspect` with no id lists runs; check `GTM_LAB_STATE_DIR` |
| `inspect` output is long | It contains every record and audit event | Claude reads the `summary` block first |

## What was and was not tested

- **Tested, real Claude Code** (2.1.330, `claude -p` print mode, project settings only, no MCP servers, permission prompts auto-denied): intro, `demo`, `inspect` via `sync`/`approve`, `explain`, `eval`, `debug`, approve refused without tool permission, approve and sync with tool permission granted, sync refused before approval.
- **Not tested:** the interactive terminal UI itself. In print mode a permission prompt cannot be answered, so granting permission was simulated with `--allowedTools`. `/gtm-lab demo --agentic` with a real key was not run.
- **Automated (CI, no Claude Code needed):** `tests/integration/test_claude_code_integration.py` checks the skill's location and frontmatter, that it only names real commands and flags, that it pre-authorizes no state change, and the CLI behavior it relies on.

# Decision-aware GTM: implementation report

Merged in PR #16 (`feat/decision-aware`, agent v2). Follow-up v2.1 in section 8.

## 1. Audit before changes

HEAD at start: `e5350b27b7d7083e6b3cf5e9c4bc71d761d61b19` (the brief reviewed `4281fe5`; only documentation changed since, PR #15). `ruff check .` clean; `pytest`: 203 passed, 2 skipped. Offline opportunity evaluations (agent v1):

| | Baseline | Checks only | Agent v1 |
|---|---|---|---|
| Main 24: relevant found / false positives / abstained | 6/8, 8/9, 1/7 | 0/8, 0/9, 7/7 | 8/8, 0/9, 7/7 |
| Held out 10: same | 2/3, 3/3, 0/4 | 0/3, 0/3, 4/4 | 2/3, 0/3, 4/4 |

Confirmed in the code: contact enrichment ran for every new qualified account with every persona, whatever the assessment; support required a literal `evidence_terms` match; disconfirmation was any successful recent-events search; the fictional web ignored the query.

## 2. What changed

| Area | Before | After |
|---|---|---|
| Enrichment | Every new qualified account, all personas | Default unchanged (`all_qualified`). Opt-in `--enrichment-strategy opportunity_gated`: the contact provider is called only when the action policy proposes it, for mapped personas only |
| Action policy | none | `policies/opportunity_action.py`, deterministic, fails closed (no assessment, baseline only, dropped claims, incomplete coverage, unmapped roles → no call). Every entry records the action, reason, personas and why, calls avoided, cost avoided and actual cost |
| Personas | all | `buying_roles[].personas`, validated against `personas` at load; an unmapped role sends the account to review instead of enriching everyone |
| Existing CRM accounts | not assessed | `--opportunity-scope all`: assessed read-only, still `skip_existing`, no write |
| Support for a pain | quote must contain an evidence term | the agent proposes; an independent constrained LLM check (quote and pain only) confirms or rejects (`SemanticPainMatch`); the term hit is a diagnostic |
| Disconfirmation | one successful recent-events search | `DisconfirmationCheck` per supported pain: the exact query must have run as a recent-events search naming the company; clearing requires every result read (or none returned: "not found in checked sources", not proof); a contradiction needs a checked quote from that search's results |
| Evaluation | query-insensitive web, opportunity labels | query-sensitive web, truncation, search errors; `gtm-lab decision-eval` compares three enrichment strategies on cost and outcome; research-quality metrics |

Not done: contact-gap plans and contact updates for existing CRM accounts (deferred; no tested interface for it); an ablation run (agent without disconfirmation, without GTM Brain); real-data validation (no independent annotator; nothing live was run on real companies).

## 3. Commands

```bash
gtm-lab decision-eval                               # free: replays evals/decisions/cassette.json
gtm-lab decision-eval --json --contact-cents 80     # strategies, research quality, metrics
gtm-lab demo --opportunity exa --enrichment-strategy opportunity_gated   # paid: Claude + Exa + contacts
gtm-lab demo --opportunity exa --opportunity-scope all                   # paid; existing accounts read-only
gtm-lab opportunity <run-id>                        # brief: assessment -> policy -> action -> spend
```

## 4. Decision evaluation (16 new cases, labeled before any v2 run)

Contact enrichment **simulated** at 80¢ per enriched account (2 PDL profiles at 28¢ plus email finding and checking). LLM and Exa costs **observed** from the recording (claude-opus-5-5, 48 turns).

| | all_qualified (default) | baseline_gated | opportunity_gated |
|---|---|---|---|
| Accounts enriched | 15 | 15 | 2 |
| Relevant accounts enriched | 4/4 | 4/4 | 2/4 |
| Enriched although not relevant or abstain expected | 11 | 11 | 0 |
| Queued for review or more research | 0 | 0 | 9 |
| Contact cost, simulated | 1200¢ | 1200¢ | 160¢ |
| LLM + Exa, observed | 0 | 0 | 105¢ + 9¢ |
| Total | 1200¢ | 1200¢ | 274¢ |
| Persona precision / recall on relevant hits | 12/16, 12/12 | 12/16, 12/12 | 6/8, 6/6 |

The decision that improved: **which accounts get paid contact enrichment.** Gating avoided all 11 enrichments of accounts that should not have been enriched (customer-onboarding role, negation, freeze found by search, incumbent tool, namesake, truncated page, search outage, sales-ops backfill, old event, generic funding, injected instruction), at 23% of the default's cost. It also enriched only the personas linked to the supported pain.

The price: **2 of 4 relevant accounts were not enriched** (`d-paraphrase-ramp`, `d-clean-after-search`). In both, the agent's disconfirmation search returned the supporting page plus an unrelated page; it opened the first, not the second, and the code credits a clearing check only when every result was read. The prompt asked to read "the relevant results": the code and the prompt disagree. This is reported, not fixed on this set; the fix (state the rule in the prompt and send back an uncredited check with the unread URLs) needs fresh cases to measure.

Opportunity labels on the same 16 cases (per-case table in `evals/decisions/REPORT.md`):

| | Baseline | Checks only (keyword rule) | Agent v2 |
|---|---|---|---|
| Relevant (5), called relevant | 5 | 1 | 3 |
| Not relevant (5), called relevant | 5 | 2 | 0 |
| Abstain expected (6), abstained | 0 | 6 | 5 |

The keyword-only control was fooled twice where the agent was not (`d-keyword-stuffing-customers`: "onboarding" and "ramp" about customers). The agent recognized both paraphrase cases (no listed term in the quote; the independent check confirmed them). Its abstain miss: `d-truncated-page`, where the relevant sentence was beyond what can be read; it returned `not_relevant` (`defer`) instead of abstaining.

Research quality: 19 searches (14 with results, 2 empty, 3 errors in the outage case), 18 pages read; support pages read 9/9, contradiction pages read 6/6, noise pages read 3; disconfirmation checks claimed 9, credited 6; observations grounded 22/23 (95% Wilson interval 79% to 99%).

Sample sizes are small (4 relevant qualified accounts); these are per-case results, not rates to generalize. Labels are one person's.

## 5. Historical benchmarks

The v1 cassettes and reports are frozen in `evals/opportunity/v1/` (cases unchanged in `evals/opportunity/cases.yaml` and `holdout.yaml`); they replay with the v1 code at `48ca5e2`. Agent v2 was not re-run on them.

## 6. Tests

- `ruff check .` clean; `pytest`: 231 passed, 2 skipped (opt-in live HubSpot, private real-data case). CI runs fixtures and replays only; no Anthropic, Exa, PDL, FullEnrich, ZeroBounce or HubSpot call.
- New tests: every action-matrix branch, not-assessed and baseline-only never unlocking spend, unmapped roles, unknown personas rejected at load, provider calls actually made per strategy, shadow estimates, existing accounts read-only, verifier verdicts (paraphrase accepted; unrelated, negation, unclear and failures rejected), superficial or invented disconfirmation not credited, unread results, empty searches, credited contradictions, and the decisions cassette replaying with the code's invariants.
- Offline lifecycle checked: `demo --offline`, `eval` (ok), `approve`, `sync` (12 ok, 2 no-ops), `sync` again (14 no-ops); `demo --offline --enrichment-strategy opportunity_gated` enriches nothing (no assessment, fail closed).
- Paid calls in this work (authorized): the decision-case recording, 105¢ LLM. No live provider other than Anthropic was called; the fictional web stood in for Exa.

## 7. Limits and next work

1. Align the disconfirmation prompt with the code rule and send back an uncredited check with its unread URLs; measure on new cases.
2. Truncation: tell the agent when a page was cut, so it abstains instead of concluding.
3. Run the comparison on real accounts, labeled independently before any agent run, through the private record/replay harness (needs approval for paid calls).
4. Ablations (no disconfirmation, no GTM Brain) on a new labeled set.
5. Contact-gap plans for existing CRM accounts, once a tested read interface for CRM contacts exists.

## 8. Follow-up: agent v2.1, measured on fresh cases

Changes after the v2 results above:

1. **Replay correctness bug.** Cassette keys did not include the system prompt or tool schemas, so after a prompt change an old recording would have replayed answers given to a different prompt without any miss. Keys now include a fingerprint of the model, prompt and tools. The v2 recording is frozen in `evals/decisions/v2/` (replays at `4e00248`).
2. **Prompt aligned with the code.** The prompt now states that a no-contradiction check counts only if every result of its search was read; an uncredited check is sent back once with the unread URLs.
3. **Truncated pages.** Pages longer than what is read are marked to the agent, and a `not_relevant` conclusion resting only on truncated pages becomes `insufficient_evidence` with `incomplete` coverage.

12 fresh cases (`evals/decisions/fresh.yaml`), labeled after these changes and before any run (41 turns, 88¢):

| | all_qualified (default) | baseline_gated | opportunity_gated |
|---|---|---|---|
| Accounts enriched | 12 | 12 | 4 |
| Relevant accounts enriched | 4/4 | 4/4 | 4/4 |
| Enriched although not relevant or abstain expected | 8 | 8 | 0 |
| Total (contacts simulated at 80¢, LLM and Exa observed) | 960¢ | 960¢ | 414¢ |
| Persona precision / recall on relevant hits | 14/16, 14/14 | 12/14, 12/14 | 12/12, 12/14 |

Opportunity labels on the fresh cases: relevant 4/4 for the agent (baseline 4/4, keyword control 0/4); not relevant called relevant 0/3 (baseline 3/3, keyword control 1/3); abstentions 5/5 (baseline 0/5, keyword control 5/5). Disconfirmation checks credited: 8 of 8 claimed.

The 16 earlier decision cases, re-recorded with v2.1 (51 turns, 116¢), are **not** an independent measure: their misses motivated the change. On them, v2.1 enriched 4 accounts (4 of 4 relevant, 0 wasted, 444¢ against 1200¢), and the truncated case now ends `insufficient_evidence`.

What actually fixed the two v2 misses: the **prompt rule**. In neither recording did the unread-URL send-back fire; the agent read every result once the prompt said so. The send-back stays as a safety net, untested by the model so far. On the fresh truncated case the agent did not conclude `not_relevant`; it returned `hypothesis_only` (`research_more`), so the new truncation rule did not have to intervene there.

Tests: 235 passed, 2 skipped. CI replays both v2.1 recordings and checks the code's invariants (no support without a checked quote and a verified semantic match; no `evidence_backed` without a credited clearing check; gating never enriches more than the default). Paid in this follow-up: 204¢ LLM, authorized.

Limits unchanged: fictional, single-labeled, small (8 relevant qualified accounts across the two decision sets); not run on real accounts.

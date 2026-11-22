# Opportunity Intelligence: implementation report

Merged in PR #12 (`feat/opportunity-intelligence`) and PR #13 (`feat/disconfirmation-search`). This report describes agent v1; its recordings and reports are frozen in `evals/opportunity/v1/`. The next version is described in `DECISION_AWARE_GTM_EVOLUTION_REPORT.md`.

## 1. Baseline audit

HEAD at start: `95414c1396edcc0b74671b3240a66b2b653e64be` (2026-11-22). Tests: 168 passed, 2 skipped (opt-in live HubSpot and private real-data case).

Already implemented and reused: PDL discovery and contacts, Exa signals, the bounded research loop (search, read, submit; step and spend caps; quote checks; trace), the ICP baseline / LLM fallback / `insufficient_evidence` split, FullEnrich, ZeroBounce, reconciliation, HubSpot, approval, idempotent sync, SQLite, `/gtm-lab`, the record/replay real-case harness.

Contract mismatches confirmed in the code before any change:

| # | Finding | Where | Status |
|---|---|---|---|
| 1 | Research evidence (`URL: "quote"`) fails the policy's substring grounding check unless the quote happens to contain a field name | `policies/rules.py` rule 4 | Fixed (typed claims) |
| 2 | A research-qualified account is always blocked: its evidence names a field the account flags as missing | rule 5 | Fixed (rule 5 reads typed resolutions) |
| 3 | Headcount research searched only the 90-day signal window | `research.py` | Fixed (`search(purpose)`, `company_facts_max_age_days`) |
| 4 | `require_signal_for_qualification` disqualified accounts that were never searched, or whose search failed or ran out of budget | `qualifier.py` | Fixed (`Account.signal_search`) |
| 5 | An enrichment cost above the provider's announced maximum was clamped, hiding the overcharge | workflow | Fixed (booked as reported, flagged, logged) |
| 6 | Run ids per second: two plans in the same second overwrote each other | workflow | Fixed (random suffix) |
| 7 | The research loop trusted the model's decision label | `research.py` | Fixed (model extracts claims; code checks, reconciles, re-runs the rules) |

## 2. Reused versus added

Reused: the Exa search and contents calls, the tool loop, budgets, trace, quote checks and subject matching (`providers/signals.py`), the record/replay approach, the rules qualifier, the store, the CLI and the skill.

Refactored, not duplicated: the loop, the tools, the budgets and page provenance moved to `agents/research_tools.py`, used by both `ResearchQualifier` and the new `OpportunityInvestigator`.

Added: GTM Brain keys in the context; `SourcedClaim`, `FactResolution`, `Observation`, `PainHypothesis`, `RoleHypothesis`, `OpportunityAssessment`; `agents/opportunity.py` (investigator and no-AI baseline); `opportunity_brief.py`; `evaluations/opportunity_eval.py`; `evals/opportunity/` (cases, cassette, report); CLI `--opportunity`, `opportunity`, `opportunity-eval`; skill path `/gtm-lab opportunity <run-id>`.

Not added, on purpose: a second orchestrator, multiple agents, a vector store, a UI, outreach.

## 3. Architecture and contracts

See `docs/opportunity-intelligence.md` (diagram, GTM Brain, statuses, next actions, coverage states) and `docs/research.md` (claims to decision). Key rules:

- ICP decision and opportunity assessment are separate records; the assessment never changes the ICP decision, and an undecided account can get at most `review`.
- Claude chooses tool calls and reports observations with a stance; code decides whether each counts (page read, quote in page, about this company, stated date in quote, an evidence term in a supporting quote) and computes status, next action, coverage and roles.
- `checked_no_result`, `incomplete`, `failed` and `not_attempted` are distinct; none is read as a negative fact about the account.

## 4. User scenario (actual commands)

```bash
gtm-lab opportunity-eval                  # free, offline: replays the recorded cassette
gtm-lab opportunity-eval --json           # metrics and per-case results
gtm-lab demo --opportunity exa ...        # paid: needs ANTHROPIC_API_KEY, EXA_API_KEY
gtm-lab opportunity <run-id>              # the brief from persisted state
```

The worked example in `docs/opportunity-intelligence.md` is real recorded output (case `relevant-sdr-onboarding`, and the contrast with `evergreen-posting`).

`gtm-lab demo --opportunity exa` was **not** run against real companies in this work: the investigator ran against the API only on the fictional evaluation web. The CLI path is covered by unit tests with a scripted client.

## 5. Grounding and failure modes

From the evaluation recording (36 agent observations after the fixes): 34 grounded, 0 unsupported (none quoted an unread page or a quote absent from the page), 1 about another company (the namesake case; rejected), 1 with a stated date absent from its quote (rejected). The injected instruction in `injection-in-page` was noted by the model and had no effect on the code's result (`hypothesis_only`).

Unit tests cover: quotes from unread pages, quotes not in the page, wrong company, stated dates not in the quote, hiring quotes without the pain's terms, support plus contradiction, undecided ICP not promoted, stopped investigations concluding nothing, URL restrictions, step and budget caps, refusals, truncation, API errors, overcharges, run-id collisions, signal search states, claim reconciliation and the policy accepting checked research.

## 6. Baseline vs agent

24 hand-labeled fictional cases. Labels written by hand by the author of the cases from the page text, before any model output was seen: one person's reading, not independent ground truth.

First recording (claude-opus-5-5, 51 turns, 120 cents, 175 s wall clock):

| | Baseline | Checks only | Agent |
|---|---|---|---|
| Relevant (8), called relevant | 6 | 1 | 7 |
| Not relevant (9), called relevant | 8 | 0 | 0 |
| Abstain expected (7), abstained | 1 | 6 | 7 |
| Observations grounded | | | 31/36 |

Two code checks were then fixed, on the same cases (no held-out set): the subject check accepts the company's full name in the page text (it had rejected pages whose title used a short name), and a failed page read is `incomplete` coverage, not `checked_no_result`. Both apply after submission, so the recording replays unchanged:

| | Baseline | Checks only | Agent |
|---|---|---|---|
| Relevant (8), called relevant | 6 | 1 | 8 |
| Not relevant (9), called relevant | 8 | 0 | 0 |
| Not relevant (9), explicitly `not_relevant` | 0 | 0 | 8 |
| Abstain expected (7), abstained | 1 | 6 | 6 |
| Abstain expected (7), called relevant | 6 | 1 | 1 |
| Observations grounded | | | 34/36 |

The subject fix recovered two relevant cases and also let one abstain case through (`contradictory-growth`): the agent read one page and did not search for the newer hiring freeze. Coverage state correct where labeled: 2/3 (`source-unavailable` is labeled `failed`; the code says `incomplete` because a search worked). Buying roles: exact match 5/8, every labeled role included 8/8 (the extra role is the sales sponsor when a quote mentions ramp time).

Cost per assessed case in this first recording: 2.1 LLM calls, 5 cents (2.7 calls, 6.4 cents after the disconfirmation rule). Latency: one recording, 175 s for 24 cases including the two no-AI assessors; not measured further.

### Follow-up: disconfirmation search and held-out cases

`evidence_backed` now requires a recent-events search (send-back once, else `needs_research`). Ten held-out cases (`evals/opportunity/holdout.yaml`) were labeled before the change and before any model run. Main set after the change: relevant 8/8, false positives 0/9, abstentions 7/7 (64 calls, 154¢ in total). Held out: baseline 3/3 false positives, agent 0/3; relevant found 2/3 for both; abstentions 4/4 for the agent, 0/4 for the baseline (28 calls, 67¢). The agent's held-out miss came from the evidence-term keyword rule (a coaching quote attached to the ramp pain), left unfixed to avoid tuning on held-out data. See `docs/agentic-vs-deterministic.md`.

## 7. Tests run

- Full suite: 203 passed, 2 skipped (live HubSpot, private real-data case), ruff clean. CI runs the same, with no network and no paid call; the opportunity evaluation replays the committed cassette.
- Paid runs in this work, all authorized by the maintainer: the first evaluation recording (120 cents), the extension after the disconfirmation rule (34 cents) and the held-out recording (67 cents).
- Not run: `demo --opportunity exa` on real companies; the private real-data research case was not re-recorded after the claims change (its cassette is stale, kept out of the repository).

## 8. Risks, debt, next slice

- Disconfirmation is now required (one recent-events search); one search is not exhaustive.
- The evidence-term keyword rule caused the held-out miss; a better check (terms per offer rather than per pain, or a reviewed list) is the next slice.
- The evaluation is small, fictional and single-labeled; next: real accounts labeled independently, a held-out split, recorded with the existing real-case harness.
- Evidence terms are keywords; paraphrased problems are missed.
- The code's next action is sometimes more eager than the model's own (`consider_persona_enrichment` where the model said `review`): worth reviewing as a policy choice.
- Subject matching by name and domain; common-word names stay ambiguous.
- The private real-data research cassette must be re-recorded (about 15 cents and 2 PDL credits).

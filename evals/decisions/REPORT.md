# Decision evaluation: which accounts get paid contact enrichment

16 hand-labeled fictional cases (labels by the case author, written before any agent v2 run). Contact enrichment simulated at 80¢ per enriched account; agent LLM and Exa costs observed from the recording.

| | all_qualified (default) | baseline_gated (signal kind) | opportunity_gated (agent + policy) |
|---|---|---|---|
| Accounts enriched | 15 | 15 | 2 |
| Contact calls avoided | 1 | 1 | 14 |
| Relevant accounts enriched | 4/4 | 4/4 | 2/4 |
| Enriched but not relevant / should abstain | 11 | 11 | 0 |
| Queued for review or research | 0 | 0 | 9 |
| Contact cost, simulated (¢) | 1200 | 1200 | 160 |
| LLM cost, observed (¢) | 0 | 0 | 105 |
| Exa cost (¢, rounded up) | 0 | 0 | 9 |
| Total (¢) | 1200 | 1200 | 274 |
| Persona precision on relevant hits | 12/16 | 12/16 | 6/8 |
| Persona recall on relevant hits | 12/12 | 12/12 | 6/6 |

Missed relevant accounts (qualified, labeled relevant, not enriched):

- all_qualified: none
- baseline_gated: none
- opportunity_gated: d-paraphrase-ramp, d-clean-after-search

Enriched although not labeled relevant:

- all_qualified: d-keyword-stuffing-customers, d-negation, d-freeze-only-via-search, d-incumbent-only-via-search, d-namesake-noise, d-truncated-page, d-provider-error-search, d-wrong-department-ops, d-old-event-dated-page, d-funding-generic, d-injection
- baseline_gated: d-keyword-stuffing-customers, d-negation, d-freeze-only-via-search, d-incumbent-only-via-search, d-namesake-noise, d-truncated-page, d-provider-error-search, d-wrong-department-ops, d-old-event-dated-page, d-funding-generic, d-injection
- opportunity_gated: none

## Research quality (agent)

- Searches: 19 (14 with results, 2 empty, 3 errors); pages read: 18
- Support pages read: 9/9; contradiction pages read: 6/6; noise pages read: 3
- Disconfirmation checks claimed: 9, credited by the code: 6
- Observations grounded: 22/23 (95% Wilson interval 79% to 99%)

## Per case

| Case | Label | Baseline | Agent | Policy | Semantic checks (verified/rejected) | Disconfirmation (credited/claimed) |
|---|---|---|---|---|---|---|
| d-paraphrase-ramp | relevant | evidence_backed | needs_research | research_more | 1/1 | 0/2 |
| d-paraphrase-coaching | relevant | evidence_backed | evidence_backed | propose_enrichment | 2/0 | 2/2 |
| d-keyword-stuffing-customers | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-negation | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-freeze-only-via-search | abstain | evidence_backed | needs_research | research_more | 1/0 | 1/1 |
| d-incumbent-only-via-search | not relevant | evidence_backed | needs_research | research_more | 1/0 | 1/1 |
| d-clean-after-search | relevant | evidence_backed | needs_research | research_more | 1/0 | 0/1 |
| d-namesake-noise | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-truncated-page | abstain | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-provider-error-search | abstain | evidence_backed | insufficient_evidence | research_more | 0/0 | 0/0 |
| d-wrong-department-ops | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-old-event-dated-page | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-funding-generic | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-relevant-leadership-ramp | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/0 | 1/1 |
| d-injection | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-not-qualified-icp | relevant | evidence_backed | evidence_backed | review | 1/0 | 1/1 |

# Opportunity evaluation: baseline vs agent

16 hand-labeled fictional cases (labels written by the case author, not independent ground truth). Same inputs for both. Agent answers replayed from a recorded cassette.

| | Baseline (signal kind, no AI) | Checks only (naive reader, no AI) | Agent (Claude, bounded research) |
|---|---|---|---|
| Relevant, called relevant | 5/5 (100%) | 1/5 (20%) | 3/5 (60%) |
| Relevant, not called relevant | 0/5 (0%) | 4/5 (80%) | 2/5 (40%) |
| Not relevant, called relevant | 5/5 (100%) | 2/5 (40%) | 0/5 (0%) |
| Not relevant, not called relevant | 0/5 (0%) | 3/5 (60%) | 5/5 (100%) |
| Not relevant, explicitly `not_relevant` | 0/5 (0%) | 0/5 (0%) | 4/5 (80%) |
| Abstain expected, abstained | 0/6 (0%) | 6/6 (100%) | 5/6 (83%) |
| Abstain expected, called relevant | 6/6 (100%) | 0/6 (0%) | 0/6 (0%) |

Agent only:

- Observations grounded (quote found in a page read, about the right company): 22/23 (96%)
- Unsupported (page not read or quote not in page): 0/23 (0%)
- Subject mismatch (page about another company): 1/23 (4%)
- Stated date not in quote: 0/23 (0%)
- Coverage state correct where labeled: 0/0
- Next action correct where labeled: 0/0
- Buying roles equal to the labeled set, on correctly found relevant cases: 0/3 (0%); include every labeled role: 3/3 (100%)
- Status different from the baseline: 13/16 (81%)
- Cost: 48 LLM calls, 105¢ LLM (per-call rounding up), $0.082 fictional Exa; 6.6¢ per case

## Per case

| Case | Label | Baseline | Checks only | Agent | Agent next action | Agent coverage |
|---|---|---|---|---|---|---|
| d-paraphrase-ramp | relevant | evidence_backed | hypothesis_only | needs_research | research_more | found |
| d-paraphrase-coaching | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| d-keyword-stuffing-customers | not relevant | evidence_backed | evidence_backed | not_relevant | defer | found |
| d-negation | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| d-freeze-only-via-search | abstain | evidence_backed | needs_research | needs_research | research_more | found |
| d-incumbent-only-via-search | not relevant | evidence_backed | evidence_backed | needs_research | research_more | found |
| d-clean-after-search | relevant | evidence_backed | needs_research | needs_research | research_more | found |
| d-namesake-noise | abstain | evidence_backed | hypothesis_only | hypothesis_only | no_relevant_evidence | checked_no_result |
| d-truncated-page | abstain | evidence_backed | hypothesis_only | not_relevant | defer | found |
| d-provider-error-search | abstain | evidence_backed | needs_research | insufficient_evidence | research_more | incomplete |
| d-wrong-department-ops | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| d-old-event-dated-page | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| d-funding-generic | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| d-relevant-leadership-ramp | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| d-injection | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| d-not-qualified-icp | relevant | evidence_backed | evidence_backed | evidence_backed | review | found |

# Decision evaluation: which accounts get paid contact enrichment

16 hand-labeled fictional cases (labels by the case author, written before any agent v2 run). Contact enrichment simulated at 80¢ per enriched account; agent LLM and Exa costs observed from the recording.

| | all_qualified (default) | baseline_gated (signal kind) | opportunity_gated (agent + policy) |
|---|---|---|---|
| Accounts enriched | 15 | 15 | 4 |
| Contact calls avoided | 1 | 1 | 12 |
| Relevant accounts enriched | 4/4 | 4/4 | 4/4 |
| Enriched but not relevant / should abstain | 11 | 11 | 0 |
| Queued for review or research | 0 | 0 | 9 |
| Contact cost, simulated (¢) | 1200 | 1200 | 320 |
| LLM cost, observed (¢) | 0 | 0 | 116 |
| Exa cost (¢, rounded up) | 0 | 0 | 8 |
| Total (¢) | 1200 | 1200 | 444 |
| Persona precision on relevant hits | 12/16 | 12/16 | 12/14 |
| Persona recall on relevant hits | 12/12 | 12/12 | 12/12 |

Missed relevant accounts (qualified, labeled relevant, not enriched):

- all_qualified: none
- baseline_gated: none
- opportunity_gated: none

Enriched although not labeled relevant:

- all_qualified: d-keyword-stuffing-customers, d-negation, d-freeze-only-via-search, d-incumbent-only-via-search, d-namesake-noise, d-truncated-page, d-provider-error-search, d-wrong-department-ops, d-old-event-dated-page, d-funding-generic, d-injection
- baseline_gated: d-keyword-stuffing-customers, d-negation, d-freeze-only-via-search, d-incumbent-only-via-search, d-namesake-noise, d-truncated-page, d-provider-error-search, d-wrong-department-ops, d-old-event-dated-page, d-funding-generic, d-injection
- opportunity_gated: none

## Research quality (agent)

- Searches: 18 (13 with results, 2 empty, 3 errors); pages read: 20
- Support pages read: 9/9; contradiction pages read: 6/6; noise pages read: 5
- Disconfirmation checks claimed: 12, credited by the code: 10
- Observations grounded: 25/26 (95% Wilson interval 81% to 99%)

## Per case

| Case | Label | Baseline | Agent | Policy | Semantic checks (verified/rejected) | Disconfirmation (credited/claimed) |
|---|---|---|---|---|---|---|
| d-paraphrase-ramp | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/1 | 2/2 |
| d-paraphrase-coaching | relevant | evidence_backed | evidence_backed | propose_enrichment | 2/0 | 2/2 |
| d-keyword-stuffing-customers | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-negation | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-freeze-only-via-search | abstain | evidence_backed | needs_research | research_more | 1/0 | 1/1 |
| d-incumbent-only-via-search | not relevant | evidence_backed | needs_research | research_more | 1/0 | 1/1 |
| d-clean-after-search | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/0 | 1/1 |
| d-namesake-noise | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-truncated-page | abstain | evidence_backed | insufficient_evidence | research_more | 0/0 | 0/0 |
| d-provider-error-search | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/2 |
| d-wrong-department-ops | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| d-old-event-dated-page | not relevant | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-funding-generic | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-relevant-leadership-ramp | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/0 | 1/1 |
| d-injection | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| d-not-qualified-icp | relevant | evidence_backed | evidence_backed | review | 2/0 | 2/2 |

# Opportunity evaluation: baseline vs agent

16 hand-labeled fictional cases (labels written by the case author, not independent ground truth). Same inputs for both. Agent answers replayed from a recorded cassette.

| | Baseline (signal kind, no AI) | Checks only (naive reader, no AI) | Agent (Claude, bounded research) |
|---|---|---|---|
| Relevant, called relevant | 5/5 (100%) | 1/5 (20%) | 5/5 (100%) |
| Relevant, not called relevant | 0/5 (0%) | 4/5 (80%) | 0/5 (0%) |
| Not relevant, called relevant | 5/5 (100%) | 2/5 (40%) | 0/5 (0%) |
| Not relevant, not called relevant | 0/5 (0%) | 3/5 (60%) | 5/5 (100%) |
| Not relevant, explicitly `not_relevant` | 0/5 (0%) | 0/5 (0%) | 3/5 (60%) |
| Abstain expected, abstained | 0/6 (0%) | 6/6 (100%) | 6/6 (100%) |
| Abstain expected, called relevant | 6/6 (100%) | 0/6 (0%) | 0/6 (0%) |

Agent only:

- Observations grounded (quote found in a page read, about the right company): 25/26 (96%)
- Unsupported (page not read or quote not in page): 0/26 (0%)
- Subject mismatch (page about another company): 1/26 (4%)
- Stated date not in quote: 0/26 (0%)
- Coverage state correct where labeled: 0/0
- Next action correct where labeled: 0/0
- Buying roles equal to the labeled set, on correctly found relevant cases: 0/5 (0%); include every labeled role: 5/5 (100%)
- Status different from the baseline: 11/16 (69%)
- Cost: 51 LLM calls, 116¢ LLM (per-call rounding up), $0.080 fictional Exa; 7.2¢ per case

## Per case

| Case | Label | Baseline | Checks only | Agent | Agent next action | Agent coverage |
|---|---|---|---|---|---|---|
| d-paraphrase-ramp | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| d-paraphrase-coaching | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| d-keyword-stuffing-customers | not relevant | evidence_backed | evidence_backed | not_relevant | defer | found |
| d-negation | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| d-freeze-only-via-search | abstain | evidence_backed | needs_research | needs_research | research_more | found |
| d-incumbent-only-via-search | not relevant | evidence_backed | evidence_backed | needs_research | research_more | found |
| d-clean-after-search | relevant | evidence_backed | needs_research | evidence_backed | consider_persona_enrichment | found |
| d-namesake-noise | abstain | evidence_backed | hypothesis_only | hypothesis_only | no_relevant_evidence | checked_no_result |
| d-truncated-page | abstain | evidence_backed | hypothesis_only | insufficient_evidence | research_more | incomplete |
| d-provider-error-search | abstain | evidence_backed | needs_research | hypothesis_only | research_more | found |
| d-wrong-department-ops | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| d-old-event-dated-page | not relevant | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| d-funding-generic | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| d-relevant-leadership-ramp | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| d-injection | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| d-not-qualified-icp | relevant | evidence_backed | evidence_backed | evidence_backed | review | found |

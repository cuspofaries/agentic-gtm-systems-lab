# Decision evaluation: which accounts get paid contact enrichment

12 hand-labeled fictional cases (labels by the case author, written before any agent v2 run). Contact enrichment simulated at 80¢ per enriched account; agent LLM and Exa costs observed from the recording.

| | all_qualified (default) | baseline_gated (signal kind) | opportunity_gated (agent + policy) |
|---|---|---|---|
| Accounts enriched | 12 | 12 | 4 |
| Contact calls avoided | 0 | 0 | 8 |
| Relevant accounts enriched | 4/4 | 4/4 | 4/4 |
| Enriched but not relevant / should abstain | 8 | 8 | 0 |
| Queued for review or research | 0 | 0 | 6 |
| Contact cost, simulated (¢) | 960 | 960 | 320 |
| LLM cost, observed (¢) | 0 | 0 | 88 |
| Exa cost (¢, rounded up) | 0 | 0 | 6 |
| Total (¢) | 960 | 960 | 414 |
| Persona precision on relevant hits | 14/16 | 12/14 | 12/12 |
| Persona recall on relevant hits | 14/14 | 12/14 | 12/14 |

Missed relevant accounts (qualified, labeled relevant, not enriched):

- all_qualified: none
- baseline_gated: none
- opportunity_gated: none

Enriched although not labeled relevant:

- all_qualified: f-not-relevant-support-onboarding, f-not-relevant-incumbent-search, f-abstain-freeze-search, f-abstain-truncated, f-abstain-namesake, f-abstain-generic-hiring, f-not-relevant-stable-team, f-abstain-search-outage
- baseline_gated: f-not-relevant-support-onboarding, f-not-relevant-incumbent-search, f-abstain-freeze-search, f-abstain-truncated, f-abstain-namesake, f-abstain-generic-hiring, f-not-relevant-stable-team, f-abstain-search-outage
- opportunity_gated: none

## Research quality (agent)

- Searches: 14 (9 with results, 1 empty, 4 errors); pages read: 15
- Support pages read: 8/8; contradiction pages read: 4/4; noise pages read: 3
- Disconfirmation checks claimed: 8, credited by the code: 8
- Observations grounded: 18/19 (95% Wilson interval 75% to 99%)

## Per case

| Case | Label | Baseline | Agent | Policy | Semantic checks (verified/rejected) | Disconfirmation (credited/claimed) |
|---|---|---|---|---|---|---|
| f-relevant-noise-result | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/1 | 2/2 |
| f-relevant-paraphrase-playbook | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/1 | 1/1 |
| f-relevant-cro-quote | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/0 | 1/1 |
| f-not-relevant-support-onboarding | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| f-not-relevant-incumbent-search | not relevant | evidence_backed | needs_research | research_more | 1/0 | 1/1 |
| f-abstain-freeze-search | abstain | evidence_backed | needs_research | research_more | 1/0 | 1/1 |
| f-abstain-truncated | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| f-abstain-namesake | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| f-abstain-generic-hiring | abstain | evidence_backed | hypothesis_only | research_more | 0/0 | 0/0 |
| f-not-relevant-stable-team | not relevant | evidence_backed | not_relevant | defer | 0/0 | 0/0 |
| f-relevant-expansion-sales | relevant | evidence_backed | evidence_backed | propose_enrichment | 1/1 | 2/2 |
| f-abstain-search-outage | abstain | evidence_backed | insufficient_evidence | research_more | 0/0 | 0/0 |

# Opportunity evaluation: baseline vs agent

12 hand-labeled fictional cases (labels written by the case author, not independent ground truth). Same inputs for both. Agent answers replayed from a recorded cassette.

| | Baseline (signal kind, no AI) | Checks only (naive reader, no AI) | Agent (Claude, bounded research) |
|---|---|---|---|
| Relevant, called relevant | 4/4 (100%) | 0/4 (0%) | 4/4 (100%) |
| Relevant, not called relevant | 0/4 (0%) | 4/4 (100%) | 0/4 (0%) |
| Not relevant, called relevant | 3/3 (100%) | 1/3 (33%) | 0/3 (0%) |
| Not relevant, not called relevant | 0/3 (0%) | 2/3 (67%) | 3/3 (100%) |
| Not relevant, explicitly `not_relevant` | 0/3 (0%) | 0/3 (0%) | 2/3 (67%) |
| Abstain expected, abstained | 0/5 (0%) | 5/5 (100%) | 5/5 (100%) |
| Abstain expected, called relevant | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) |

Agent only:

- Observations grounded (quote found in a page read, about the right company): 18/19 (95%)
- Unsupported (page not read or quote not in page): 0/19 (0%)
- Subject mismatch (page about another company): 1/19 (5%)
- Stated date not in quote: 0/19 (0%)
- Coverage state correct where labeled: 0/0
- Next action correct where labeled: 0/0
- Buying roles equal to the labeled set, on correctly found relevant cases: 0/4 (0%); include every labeled role: 4/4 (100%)
- Status different from the baseline: 8/12 (67%)
- Cost: 41 LLM calls, 88¢ LLM (per-call rounding up), $0.055 fictional Exa; 7.3¢ per case

## Per case

| Case | Label | Baseline | Checks only | Agent | Agent next action | Agent coverage |
|---|---|---|---|---|---|---|
| f-relevant-noise-result | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| f-relevant-paraphrase-playbook | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| f-relevant-cro-quote | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| f-not-relevant-support-onboarding | not relevant | evidence_backed | evidence_backed | not_relevant | defer | found |
| f-not-relevant-incumbent-search | not relevant | evidence_backed | needs_research | needs_research | research_more | found |
| f-abstain-freeze-search | abstain | evidence_backed | needs_research | needs_research | research_more | found |
| f-abstain-truncated | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| f-abstain-namesake | abstain | evidence_backed | hypothesis_only | hypothesis_only | no_relevant_evidence | checked_no_result |
| f-abstain-generic-hiring | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| f-not-relevant-stable-team | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| f-relevant-expansion-sales | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| f-abstain-search-outage | abstain | evidence_backed | needs_research | insufficient_evidence | research_more | incomplete |

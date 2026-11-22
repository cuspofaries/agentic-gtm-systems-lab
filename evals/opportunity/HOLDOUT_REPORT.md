# Opportunity evaluation: baseline vs agent

10 hand-labeled fictional cases (labels written by the case author, not independent ground truth). Same inputs for both. Agent answers replayed from a recorded cassette.

| | Baseline (signal kind, no AI) | Checks only (naive reader, no AI) | Agent (Claude, bounded research) |
|---|---|---|---|
| Relevant, called relevant | 2/3 (67%) | 0/3 (0%) | 2/3 (67%) |
| Relevant, not called relevant | 1/3 (33%) | 3/3 (100%) | 1/3 (33%) |
| Not relevant, called relevant | 3/3 (100%) | 0/3 (0%) | 0/3 (0%) |
| Not relevant, not called relevant | 0/3 (0%) | 3/3 (100%) | 3/3 (100%) |
| Not relevant, explicitly `not_relevant` | 0/3 (0%) | 0/3 (0%) | 2/3 (67%) |
| Abstain expected, abstained | 0/4 (0%) | 4/4 (100%) | 4/4 (100%) |
| Abstain expected, called relevant | 4/4 (100%) | 0/4 (0%) | 0/4 (0%) |

Agent only:

- Observations grounded (quote found in a page read, about the right company): 16/17 (94%)
- Unsupported (page not read or quote not in page): 0/17 (0%)
- Subject mismatch (page about another company): 1/17 (6%)
- Stated date not in quote: 0/17 (0%)
- Coverage state correct where labeled: 1/1 (100%)
- Next action correct where labeled: 0/0
- Buying roles equal to the labeled set, on correctly found relevant cases: 0/2 (0%); include every labeled role: 2/2 (100%)
- Status different from the baseline: 9/10 (90%)
- Cost: 28 LLM calls, 67¢ LLM (per-call rounding up), $0.038 fictional Exa; 6.7¢ per case

## Per case

| Case | Label | Baseline | Checks only | Agent | Agent next action | Agent coverage |
|---|---|---|---|---|---|---|
| ho-relevant-coaching | relevant | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| ho-relevant-but-freeze-later | abstain | evidence_backed | hypothesis_only | needs_research | research_more | found |
| ho-relevant-search-only | relevant | insufficient_evidence | insufficient_evidence | evidence_backed | consider_persona_enrichment | found |
| ho-relevant-funding-sales | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| ho-not-relevant-partner-role | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| ho-not-relevant-incumbent | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| ho-not-relevant-old-funding | not relevant | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| ho-abstain-namesake | abstain | evidence_backed | hypothesis_only | hypothesis_only | no_relevant_evidence | checked_no_result |
| ho-abstain-generic | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| ho-abstain-unreadable | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | incomplete |

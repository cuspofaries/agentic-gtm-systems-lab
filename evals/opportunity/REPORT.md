# Opportunity evaluation: baseline vs agent

24 hand-labeled fictional cases (labels written by the case author, not independent ground truth). Same inputs for both. Agent answers replayed from a recorded cassette.

| | Baseline (signal kind, no AI) | Checks only (naive reader, no AI) | Agent (Claude, bounded research) |
|---|---|---|---|
| Relevant, called relevant | 6/8 (75%) | 1/8 (12%) | 8/8 (100%) |
| Relevant, not called relevant | 2/8 (25%) | 7/8 (88%) | 0/8 (0%) |
| Not relevant, called relevant | 8/9 (89%) | 0/9 (0%) | 0/9 (0%) |
| Not relevant, not called relevant | 1/9 (11%) | 9/9 (100%) | 9/9 (100%) |
| Not relevant, explicitly `not_relevant` | 0/9 (0%) | 0/9 (0%) | 8/9 (89%) |
| Abstain expected, abstained | 1/7 (14%) | 6/7 (86%) | 6/7 (86%) |
| Abstain expected, called relevant | 6/7 (86%) | 1/7 (14%) | 1/7 (14%) |

Agent only:

- Observations grounded (quote found in a page read, about the right company): 34/36 (94%)
- Unsupported (page not read or quote not in page): 0/36 (0%)
- Subject mismatch (page about another company): 1/36 (3%)
- Stated date not in quote: 1/36 (3%)
- Coverage state correct where labeled: 2/3 (67%)
- Next action correct where labeled: 1/1 (100%)
- Buying roles equal to the labeled set, on correctly found relevant cases: 5/8 (62%); include every labeled role: 8/8 (100%)
- Status different from the baseline: 16/24 (67%)
- Cost: 51 LLM calls, 120¢ LLM (per-call rounding up), $0.077 fictional Exa; 5.0¢ per case

## Per case

| Case | Label | Baseline | Checks only | Agent | Agent next action | Agent coverage |
|---|---|---|---|---|---|---|
| relevant-sdr-onboarding | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| relevant-enablement-manager | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| relevant-new-vp-ramp | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| relevant-only-in-page | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| generic-ae-posting | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| wrong-department-support | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| wrong-department-marketing | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| evergreen-posting | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| old-event-new-page-date | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| namesake-company | abstain | evidence_backed | hypothesis_only | hypothesis_only | no_relevant_evidence | checked_no_result |
| funding-no-sales-plan | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| funding-with-sales-expansion | relevant | evidence_backed | hypothesis_only | evidence_backed | consider_persona_enrichment | found |
| incumbent-tool | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| contradictory-growth | abstain | evidence_backed | evidence_backed | evidence_backed | consider_persona_enrichment | found |
| layoffs-in-sales | not relevant | insufficient_evidence | insufficient_evidence | not_relevant | defer | found |
| snippet-overclaims | not relevant | evidence_backed | hypothesis_only | not_relevant | defer | found |
| no-signal-qualified | abstain | insufficient_evidence | insufficient_evidence | insufficient_evidence | no_relevant_evidence | checked_no_result |
| source-unavailable | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | incomplete |
| budget-exhausted | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | incomplete |
| relevant-found-by-search | relevant | insufficient_evidence | insufficient_evidence | evidence_backed | consider_persona_enrichment | found |
| injection-in-page | abstain | evidence_backed | hypothesis_only | hypothesis_only | research_more | found |
| ramp-without-hiring | relevant | insufficient_evidence | insufficient_evidence | evidence_backed | consider_persona_enrichment | found |
| undecided-icp-relevant | relevant | evidence_backed | evidence_backed | evidence_backed | review | found |
| stale-posting | not relevant | evidence_backed | hypothesis_only | hypothesis_only | no_relevant_evidence | checked_no_result |

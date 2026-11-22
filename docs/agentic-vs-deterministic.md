# What the agent decides, and what proves it helps

## Responsibility split

| Step | Who decides | Why |
|---|---|---|
| Discovery, signal search, contact search, email checks | Code calling providers | Fixed queries; nothing to judge |
| ICP fit | Deterministic rules | Firmographic thresholds are rules; an LLM adds nothing but variance |
| Missing company facts (`--research`) | Claude finds and quotes; code checks, reconciles, re-runs the rules | Finding a headcount on the web needs search choices; deciding the ICP does not |
| Whether an event relates to the offer's problem (`--opportunity`) | Claude reads and judges each page; code checks each claim and computes status and next action | Telling "rebuilding onboarding to cut ramp time" from "evergreen posting, not hiring" needs reading, which keyword rules do badly (measured below) |
| Policies, budget, approval, CRM writes | Code and a person | Not delegable |

Claude never decides the ICP, never writes to the CRM, and its proposed next action is recorded but not used.

## Evidence: baseline vs agent on 24 labeled cases (before the disconfirmation rule)

Agent v1, recorded in `evals/opportunity/v1/` (replays with the v1 code at commit `48ca5e2`). Cases in `evals/opportunity/cases.yaml`: fictional companies, a fictional web, labels written by hand by the case author from the page text before any model output was seen. One person's labels, not independent ground truth.

Three assessors on the same inputs:

- **Baseline (no AI):** a fresh signal whose kind may suggest one of the offer's pains = relevant.
- **Checks only (no AI):** reads the first signal page and quotes its first sentence as support; the same code checks apply. It measures what the checks do on their own.
- **Agent:** Claude Opus 5.5 with search and read, then the same checks.

| | Baseline | Checks only | Agent |
|---|---|---|---|
| Relevant (8), called relevant | 6 | 1 | 8 |
| Not relevant (9), called relevant | 8 | 0 | 0 |
| Not relevant (9), explicitly `not_relevant` | 0 | 0 | 8 |
| Abstain expected (7), abstained | 1 | 6 | 6 |
| Abstain expected (7), called relevant | 6 | 1 | 1 |

Agent observations: 36, of which 34 grounded (quote in a page read, about the right company), 0 unsupported, 1 about another company (the namesake case, correctly rejected), 1 with a stated date not in its quote. Cost: 51 LLM calls, 120¢ (5¢ per case), $0.077 of fictional Exa calls. The recording took 175 s for all 24 cases (one run, wall clock).

Reading:

- The baseline's failure is false positives: it called 8 of 9 not-relevant cases relevant (support role, marketing role, evergreen posting, old event, funding for engineering, incumbent tool, misleading snippet, stale posting).
- The code checks alone remove those false positives but also lose 7 of 8 relevant cases: without reading and choosing the right sentence, the evidence-term rule rarely fires. Precision comes from the checks; recall comes from the agent.
- The agent found all 8 relevant cases, including two the baseline missed (a company blog found by search, a ramp-time problem without a hiring signal), and marked 8 of 9 not-relevant cases explicitly `not_relevant`.
- Its one wrong call (`contradictory-growth`): it read the signal page ("plans to double its sales team and is building an onboarding program") and submitted without searching; a newer page about a hiring freeze was only reachable by search. Nothing in the code requires a disconfirming search yet.

### Changes made after seeing results

The first recording gave the agent 7/8 relevant found, 0/9 false positives, 7/7 abstentions, 31/36 grounded observations (kept in `OPPORTUNITY_INTELLIGENCE_REPORT.md`). Reading it showed two code problems, fixed on the same cases:

- The subject check required the company's full name in the page title or the quote, and rejected correct pages whose title used a short name ("Talvo names new VP of Sales"). It now also accepts the full name in the page text read; the namesake case still fails it.
- A failed page read, with a search that worked, gave coverage `checked_no_result` ("checked, nothing found"). It now gives `incomplete`.

Both checks apply after Claude submits, so the same recording replays unchanged. But there is no held-out set: the second numbers were measured on cases that informed the fix, and the first fix also let one abstain case through (`contradictory-growth`, previously rejected for the wrong reason). Treat the table as a description of behavior on these cases, not an accuracy estimate.

### Other limits of this evaluation

- 24 cases, fictional pages written to exercise known failure modes; real pages are longer and messier.
- `search` returns the case's pages whatever the query, so query quality is not tested.
- Labels: buying roles match the labeled set exactly in 5 of 8 relevant cases and include every labeled role in 8 of 8; the extra role is the sales sponsor when a quote mentions ramp time, which the labels did not anticipate.
- No business impact (pipeline, revenue) is claimed or measured.

### Disconfirmation search, then a held-out set

After the evaluation above, the code was changed to require a disconfirmation search: a submission with supporting observations is sent back once if no recent-events search has run, and supported pains without one give `needs_research`. Before this change, 10 new cases were written and labeled (`evals/opportunity/holdout.yaml`), not seen by any model run and not used to tune anything.

On the 24 cases (13 new turns recorded where the send-back changed the conversation): the agent now abstains on all 7 abstain cases, including `contradictory-growth`, where it searched, found the freeze and returned `needs_research`; relevant found 8/8, false positives 0/9. 64 LLM calls in total, 154¢.

On the 10 held-out cases (recorded once, 28 turns, 67¢):

| | Baseline | Checks only | Agent |
|---|---|---|---|
| Relevant (3), called relevant | 2 | 0 | 2 |
| Not relevant (3), called relevant | 3 | 0 | 0 |
| Abstain expected (4), abstained | 0 | 4 | 4 |

- `ho-relevant-but-freeze-later` (support on the signal page, a hiring freeze only on a newer page): the send-back made the agent search, it found the freeze, and the code returned `needs_research`. Without the rule this is the case the agent got wrong before.
- The agent's miss (`ho-relevant-coaching`) is the keyword rule's, not the model's: the quote "we want a standard coaching playbook so new hires reach quota faster" was attached to the `slow-ramp` pain, whose evidence terms ("ramp", "quota attainment"...) it does not contain, so the code made it `unresolved`. The model's own view was that it matched both pains. Fixing the term list after seeing this would be tuning on the held-out set; it is left as a known limit.
- `ho-not-relevant-old-funding` was not called relevant, but as `hypothesis_only`, not `not_relevant`: the model marked the 2022 round `unresolved`, not `contradicts`.

10 cases is too few for rates; the point is that no new failure mode appeared on unseen cases and the one targeted failure was fixed.

## Agent v2 and the enrichment decision

The results above are agent v1 (frozen in `evals/opportunity/v1/`). Agent v2 replaced the keyword gate with an independent semantic check, made disconfirmation an audited list of checks, and added the opportunity action policy that decides paid contact enrichment. It was evaluated on 16 new cases (`evals/decisions/cases.yaml`), labeled before any v2 run, on a query-sensitive fictional web (a page comes back only for a query that names the company and matches its topic), with truncated pages, search errors and namesake noise. `gtm-lab decision-eval` replays it for free.

Which accounts get paid contact enrichment (contacts simulated at 80¢ per account; LLM and Exa observed):

| | Enrich every qualified account (default) | Gate on the signal kind | Gate on the agent's assessment |
|---|---|---|---|
| Enriched | 15 | 15 | 2 |
| Relevant enriched | 4 of 4 | 4 of 4 | 2 of 4 |
| Enriched but not relevant or abstain expected | 11 | 11 | 0 |
| Total cost | 1200¢ | 1200¢ | 274¢ (160 contacts + 105 LLM + 9 Exa) |

Opportunity labels on the same cases:

| | Signal-kind baseline | Keyword rule (no model) | Agent v2 |
|---|---|---|---|
| Relevant (5), called relevant | 5 | 1 | 3 |
| Not relevant (5), called relevant | 5 | 2 | 0 |
| Abstain expected (6), abstained | 0 | 6 | 5 |

- Both paraphrase cases (the problem stated in words that are not in the keyword list) were recognized; the keyword rule found neither, and it accepted a customer-onboarding page that the agent did not.
- The two relevant accounts the gate missed were both caused by one rule: a clearing check counts only if every search result was read, and the agent left an unrelated result unopened. The prompt asks for "relevant results", the code for all of them. Not fixed on this set.
- One abstain miss: a truncated page (the relevant sentence was past what can be read); the agent concluded `not_relevant`.
- Observations grounded: 22 of 23; disconfirmation checks credited by the code: 6 of 9 claimed.

These are 16 cases with 4 relevant qualified accounts: per-case evidence, not a rate. That v2 recording is frozen in `evals/decisions/v2/`.

### v2.1 on 12 fresh cases

The prompt now states the code's rule (read every result before claiming no contradiction), truncated pages are marked, and replay keys include the prompt. On 12 new cases labeled before any run (`evals/decisions/fresh.yaml`, `gtm-lab decision-eval --cases evals/decisions/fresh.yaml --cassette evals/decisions/fresh-cassette.json`):

| | Enrich every qualified account | Gate on the signal kind | Gate on the agent's assessment |
|---|---|---|---|
| Enriched | 12 | 12 | 4 |
| Relevant enriched | 4 of 4 | 4 of 4 | 4 of 4 |
| Wasted enrichments | 8 | 8 | 0 |
| Total cost | 960¢ | 960¢ | 414¢ |

No miss and no wasted enrichment on these 12; the agent abstained on all 5 abstain cases. The fix that mattered was the prompt sentence: the code's send-back for unread results never had to fire. 12 cases is still small. Full report: `DECISION_AWARE_GTM_EVOLUTION_REPORT.md`.

## Answer to the two questions

**What does the agent decide that fixed rules cannot decide as well?** Whether a company event is evidence of the specific problem the product solves, and therefore whether an account is worth paid contact enrichment and for which roles: reading the page behind a hiring or funding signal, finding the sentence that ties it to onboarding or ramp time, or the one that rules it out (evergreen, wrong team, namesake, old, incumbent), and abstaining when the pages do not settle it.

**What evidence shows it is better?** On the 24 labeled cases above, against the same inputs: the signal-kind baseline made 8 false positives out of 9 not-relevant cases, the agent none, while finding 8 of 8 relevant cases to the baseline's 6; on 10 held-out cases, 3 of 3 false positives for the baseline and none for the agent, 2 of 3 relevant cases found by each. On 16 new decision cases with agent v2, gating enrichment on its assessment bought 2 enrichments instead of 15, avoided all 11 wasted ones and cost 274¢ instead of 1200¢, but missed 2 of 4 relevant accounts; after aligning the prompt with the code's rule, on 12 fresh cases it bought 4 enrichments instead of 12, all 4 relevant, none wasted, 414¢ instead of 960¢. The evidence is small, fictional and labeled by one person, and one fix was made after seeing the results; it is a reason to run the same comparison on real, independently labeled accounts, not a performance claim.

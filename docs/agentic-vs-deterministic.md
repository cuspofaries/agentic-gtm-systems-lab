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

## Evidence: baseline vs agent on 24 labeled cases

`gtm-lab opportunity-eval` (free, offline; replays `evals/opportunity/cassette.json`). Cases in `evals/opportunity/cases.yaml`: fictional companies, a fictional web, labels written by hand by the case author from the page text before any model output was seen. One person's labels, not independent ground truth.

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

## Answer to the two questions

**What does the agent decide that fixed rules cannot decide as well?** Whether a company event is evidence of the specific problem the product solves: reading the page behind a hiring or funding signal, finding the sentence that ties it to onboarding or ramp time, or the one that rules it out (evergreen, wrong team, namesake, old, incumbent), and abstaining when the pages do not settle it.

**What evidence shows it is better?** On the 24 labeled cases above, against the same inputs: the signal-kind baseline made 8 false positives out of 9 not-relevant cases, the agent none, while finding 8 of 8 relevant cases to the baseline's 6. The evidence is small, fictional and labeled by one person, and one fix was made after seeing the results; it is a reason to run the same comparison on real, independently labeled accounts, not a performance claim.

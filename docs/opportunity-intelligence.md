# Opportunity intelligence

`--opportunity exa` answers a question the ICP rules cannot: **does this account show the problem our offer solves, now, and who might care?** It runs on new, ICP-qualified accounts, before any paid contact enrichment, and produces an Account Opportunity Brief. It never changes the ICP decision, never enriches contacts, never writes to the CRM and never sends anything.

## The business case

The example company sells a sales-onboarding platform. A US SaaS company with 85 employees fits the ICP, and a signal search finds an SDR job posting.

- A signal-kind rule says: hiring signal, onboarding offer, relevant. Enrich all personas.
- The page may say the opposite: the posting is evergreen, the role is in support, the company is a namesake, the event dates from 2023, the company already uses an onboarding platform, or a hiring freeze followed.
- What makes it relevant is text that ties the event to the problem: "we are rebuilding onboarding to cut ramp time".

The investigator reads the pages, looks for that text and for the misleading explanations, and is allowed to abstain. The code checks everything it reports.

## Where it sits

```
discovery ──► signals (optional, --signals exa) ──► ICP rules ──► CRM reconciliation
                                                      │                 │
                                              research loop      new, qualified accounts
                                            (--research: missing      │
                                             company facts only)      ▼
                                                              opportunity assessment  ◄── GTM Brain
                                                              (--opportunity exa,          (offers, pains,
                                                               bounded, per account)        roles, signal
                                                                      │                     interpretation)
                                                                      ▼
                                                     Account Opportunity Brief (no write)
                                                                      │
                                    contact enrichment ──► policies ──► human approval ──► sync
```

ICP fit and opportunity relevance are two decisions with two records: `QualificationDecision` (unchanged) and `OpportunityAssessment` (new, linked by account id).

## The GTM Brain

Optional keys in `gtm-context.yaml` (see the example config and [`configuration.md`](configuration.md)):

- `offers`: the product, the problem it solves, its pain ids, exclusions (for example "already uses a dedicated onboarding platform") and the evidence expected.
- `pain_points`: each pain with `evidence_terms` (words a supporting quote must contain, such as "ramp" or "onboarding"), supporting and weakening events.
- `buying_roles`: roles linked to pains (problem owner, sponsor, stakeholder, user) with title hints and a reason. They are hypotheses about who might care, never people.
- `signal_interpretation`: which pains a signal kind may suggest, and the misleading alternatives to test.
- `research_policy`: `opportunity_max_accounts` (default 3 per run), `opportunity_max_steps` (default 8 tool calls per account), `company_facts_max_age_days` (default 730).

Unknown pain ids are rejected when the context loads.

## What the agent decides, what the code decides

| Decided by Claude | Decided by code |
|---|---|
| Which pages to read (including signal pages already found), what to search, when to stop | What a tool may do: only URLs from a search or a known signal page can be read; search windows; budgets checked before each call |
| Which observations to report, with a stance per pain (supports, contradicts, irrelevant, unresolved) | Whether each observation counts: page read in this run, quote in its text, page about this company, a stated event date present in the quote, an evidence term of the pain in a supporting quote |
| Whether to look for contrary news, and where | That it must: `evidence_backed` requires a recent-events search; a submission with support and no such search is sent back once, and without one supported pains give `needs_research` |
| Alternative explanations and unknowns (kept as its text, labeled) | Each pain's status, the assessment status, the coverage state, the next action, the buying roles |
| Its own proposed next action (recorded in `agent_view`, not used) | Whether the ICP decision changes: never |

Assessment statuses: `evidence_backed`, `needs_research` (support and contradiction, or support with no disconfirmation search), `not_relevant` (checked sources point away), `hypothesis_only` (a signal suggests a pain, nothing checked confirms it), `insufficient_evidence`.

Next actions: `consider_persona_enrichment` (evidence-backed and ICP-qualified; a suggestion, enrichment stays a human decision), `review`, `research_more`, `defer`, `no_relevant_evidence`. An undecided ICP account (`--opportunity-include-undecided`) can at most get `review`.

Coverage: `found`, `checked_no_result`, `incomplete` (budget ran out, or a source failed while others worked), `failed` (every call failed), `not_attempted`. A search with no result, a failed page or an exhausted budget is never read as "this account does not have the problem": `no_relevant_evidence` means "nothing found", not "out of market".

## Use

```bash
export EXA_API_KEY=... ANTHROPIC_API_KEY=...
gtm-lab demo --discovery pdl --signals exa --opportunity exa --max-llm-cents 50 \
  --opportunity-exa-millidollars 50 --example-dir path/to/your-context-dir
gtm-lab opportunity <run-id>          # the brief, Markdown
gtm-lab opportunity <run-id> --json   # the same, structured
```

`demo` also writes `opportunity_<run-id>.md` next to the review artifact, and `gtm-lab inspect` includes `opportunity_assessments`. In Claude Code: `/gtm-lab opportunity <run-id>` (read-only).

Cost: Claude is called a few times per assessed account (measured in the evaluation: 2.7 calls and 6.4¢ on average), Exa searches $0.004 and page reads $0.001. Both caps are per run and checked before each call.

## Worked example (real output)

From the evaluation, case `relevant-sdr-onboarding` (fictional company and page, Claude's answer recorded from the API and replayed). The page says: "Brightline Metrics is hiring six Sales Development Representatives as we double the sales team this year. New reps join a four-week onboarding program; we are rebuilding it to cut ramp time from five months to three."

- Steps: `read` of the signal page; a first `submit`, sent back by the code because no recent-events search had run; a `search` (purpose `recent_events`, 1 result); the final `submit`.
- Observations, both `quote_found` on `page-1`:
  - `obs-1` supports `slow-ramp`: "we are rebuilding it to cut ramp time from five months to three"
  - `obs-2` unresolved for `onboarding-at-scale`: "hiring six Sales Development Representatives as we double the sales team this year" (a hiring quote without an evidence term is not support)
- Status `evidence_backed`, next action `consider_persona_enrichment`, roles: enablement owner, sales sponsor, RevOps stakeholder (hypotheses).
- Claude's alternative explanations: "The posting may be evergreen. The page text gives no date, and only the search metadata shows 2026-11-16." Unknowns include "The recent-events search found no other news, so there is no outside confirmation either way".
- Claude's own proposal was `review`; the code's rule gave `consider_persona_enrichment`. The code is the more eager of the two here.
- Cost: 4 LLM calls, 10¢.

The baseline also said `evidence_backed` here. Where they differ, case `evergreen-posting`: the baseline calls the SDR posting relevant; the agent read "we are not hiring for a specific opening right now", reported it as contradicting, and the code returned `not_relevant` → `defer`.

## Measured

See [`agentic-vs-deterministic.md`](agentic-vs-deterministic.md) and the generated [`evals/opportunity/REPORT.md`](../evals/opportunity/REPORT.md). In short, on 24 hand-labeled fictional cases: the baseline called 8 of 9 not-relevant cases relevant; the agent called none of them relevant and found all 8 relevant ones. A first wrong call (no disconfirming search) led to the rule that requires one; on 10 held-out cases the agent made no false positive and missed one relevant case because of the keyword rule.

## Risk boundaries

- Read-only tools; no URL outside a search result or a known signal page is fetched.
- Page text is passed as untrusted data; a page that tells the model what to conclude is still checked like any other (case `injection-in-page`: the model noted the embedded instruction and the code returned `hypothesis_only`).
- A quote proves what a page says, not that it is true.
- Roles are from the GTM Brain, never people or contact details; nothing is enriched or contacted.
- Approval, idempotent sync and HubSpot checks are unchanged.

## Limits

- The subject check matches names and domains; a common-word company name can still match unrelated pages.
- A disconfirmation search is required before `evidence_backed`, but one search is not exhaustive coverage.
- The evidence-term rule is a keyword list: a page that describes the problem in other words is not counted as support.
- The evaluation is small, fictional and labeled by one person; it shows behavior, not business impact.

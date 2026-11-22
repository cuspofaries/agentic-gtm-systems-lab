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
                                                     opportunity action policy (code)
                                                     propose_enrichment / defer /
                                                     research_more / review
                                                                      │
                                     Account Opportunity Brief (no write)
                                                                      │
        contact enrichment (gated: proposed accounts, mapped personas) ──► policies ──► human approval ──► sync
```

ICP fit and opportunity relevance are two decisions with two records: `QualificationDecision` (unchanged) and `OpportunityAssessment` (new, linked by account id).

## The GTM Brain

Optional keys in `gtm-context.yaml` (see the example config and [`configuration.md`](configuration.md)):

- `offers`: the product, the problem it solves, its pain ids, exclusions (for example "already uses a dedicated onboarding platform") and the evidence expected.
- `pain_points`: each pain with `evidence_terms` (typical words such as "ramp" or "onboarding", recorded as a diagnostic; support is decided by the semantic check), supporting and weakening events.
- `buying_roles`: roles linked to pains (problem owner, sponsor, stakeholder, user) with title hints, a reason, and `personas`: the contact-provider personas to enrich for that role in gated mode. They are hypotheses about who might care, never people.
- `signal_interpretation`: which pains a signal kind may suggest, and the misleading alternatives to test.
- `research_policy`: `opportunity_max_accounts` (default 3 per run), `opportunity_max_steps` (default 8 tool calls per account), `company_facts_max_age_days` (default 730).

Unknown pain ids and persona names are rejected when the context loads.

## What the agent decides, what the code decides

| Decided by Claude | Decided by code |
|---|---|
| Which pages to read (including signal pages already found), what to search, when to stop | What a tool may do: only URLs from a search or a known signal page can be read; search windows; budgets checked before each call |
| Which observations to report, with a stance per pain and a rationale | Whether each observation counts: page read in this run, quote in its text, page about this company, a stated event date present in the quote |
| That a quote expresses a pain, in any wording | Whether that reading stands: an independent, constrained LLM check sees only the quote and the pain and answers expresses / negates / unrelated / unclear (`SemanticPainMatch`); only `expresses` counts, failures fail closed; the keyword hit is recorded as a diagnostic |
| Which competing explanation to test and with which search | Whether the check is credited (an uncredited check with unread results is sent back once with those URLs) (`DisconfirmationCheck`): the exact query ran as a recent-events search naming the company, and every result was read (or none came back: "not found in checked sources", not proof); a contradiction needs a checked quote from that search's results. `evidence_backed` needs a credited clearing check for the supported pain; a submission without one is sent back once |
| Alternative explanations and unknowns (kept as its text, labeled) | Each pain's status, the assessment status, the coverage state, the next action, the buying roles |
| Its own proposed next action (recorded in `agent_view`, not used) | Whether the ICP decision changes: never |

Assessment statuses: `evidence_backed`, `needs_research` (support and contradiction, or support without a credited disconfirmation check), `not_relevant` (checked sources point away), `hypothesis_only` (a signal suggests a pain, nothing checked confirms it), `insufficient_evidence`.

Next actions: `consider_persona_enrichment` (evidence-backed and ICP-qualified; a suggestion, enrichment stays a human decision), `review`, `research_more`, `defer`, `no_relevant_evidence`. An undecided ICP account (`--opportunity-include-undecided`) can at most get `review`.

Coverage: `found`, `checked_no_result`, `incomplete` (budget ran out, or a source failed while others worked), `failed` (every call failed), `not_attempted`. A search with no result, a failed page or an exhausted budget is never read as "this account does not have the problem": `no_relevant_evidence` means "nothing found", not "out of market".

## From assessment to spend: opportunity-gated enrichment

By default (`--enrichment-strategy all_qualified`) every new ICP-qualified account is enriched with every persona, as before; when an investigator ran, each entry also records a shadow estimate of what gating would have done. With `--enrichment-strategy opportunity_gated`, a deterministic action policy (`policies/opportunity_action.py`) decides from the agent's checked assessment, and the contact provider is called only for `propose_enrichment`:

| Assessment | Action | Contact provider |
|---|---|---|
| `evidence_backed`, coverage `found`, no dropped claim, roles mapped to personas | `propose_enrichment` | called, mapped personas only |
| `not_relevant` | `defer` | not called |
| `needs_research`, `hypothesis_only` | `research_more` | not called |
| `insufficient_evidence` | `review`, or `research_more` if coverage was incomplete or failed | not called |
| no agent assessment (cap reached, not selected, baseline only) | `review` ("not assessed" is never "not relevant") | not called |
| dropped claims, a supported role without a persona mapping | `review` | not called |
| ICP not qualified | `review`; opportunity evidence never promotes it | not called |

Personas come from `buying_roles[].personas` (validated against `personas`). Deferred accounts are skipped entries: they are not proposed for a CRM write. Each entry carries an `OpportunityAction` (status, action, reason, personas and why, calls avoided, cost avoided at the provider's announced maximum, actual cost), shown in `gtm-lab opportunity <run-id>`. A proposed enrichment still goes through budgets, policies and human approval.

`--opportunity-scope all` also assesses accounts already in the CRM, read-only: they stay `skip_existing`, nothing is written or enriched. Contact-gap plans and contact updates for existing accounts are not implemented.

## Use

```bash
export EXA_API_KEY=... ANTHROPIC_API_KEY=...
gtm-lab demo --discovery pdl --signals exa --opportunity exa --max-llm-cents 50 \
  --opportunity-exa-millidollars 50 --enrichment-strategy opportunity_gated \
  --example-dir path/to/your-context-dir
gtm-lab opportunity <run-id>          # the brief, Markdown
gtm-lab opportunity <run-id> --json   # the same, structured
```

`demo` also writes `opportunity_<run-id>.md` next to the review artifact, and `gtm-lab inspect` includes `opportunity_assessments`. In Claude Code: `/gtm-lab opportunity <run-id>` (read-only).

Cost: Claude is called a few times per assessed account (measured in the evaluation: 2.7 calls and 6.4¢ on average), Exa searches $0.004 and page reads $0.001. Both caps are per run and checked before each call.

## Worked example (real output)

From the decision evaluation, case `d-paraphrase-coaching` (fictional company and page; Claude's answers recorded from the API and replayed). The signal page says: "Silverline Desk is growing its SDR team from 4 to 12. Every new SDR is currently taught differently depending on who sits next to them; this lead will create one consistent way to get new reps productive." None of the pain's keywords appears.

- Steps: `read` of the signal page; `search` (recent_events) "Silverline Desk sales enablement platform hiring freeze layoffs", 1 result.
- Observations, both `quote_found`: "Every new SDR is currently taught differently depending on who sits next to them" (supports `onboarding-at-scale`) and "create one consistent way to get new reps productive" (supports `slow-ramp`).
- Semantic checks: both `verified` by the independent check, `lexical_term_hit: false`. Its reason for the first: "each new SDR is trained differently depending on who sits next to them. That means sales-hire onboarding has no standard, repeatable process."
- Disconfirmation: two checks on that search, both credited ("all 1 result(s) read").
- Status `evidence_backed`; policy `propose_enrichment` for Sales Enablement, VP Sales, Head of Sales, Revenue Operations. Claude's own proposal was `review`.
- Cost: 5 LLM calls (including 2 verifications), 9¢; Exa $0.005.

Contrast, `d-freeze-only-via-search`: the signal page supports slow ramp ("redesigning sales onboarding because new reps take too long to ramp", verified). The agent's recent-events search returned a newer page, which it read: "Ember Analytics froze sales hiring this week and paused its onboarding redesign." The contradiction is credited, the status is `needs_research`, the policy says `research_more`, and no contact is bought. The signal-kind baseline would have enriched it.

## Measured

On 12 fresh decision cases (`gtm-lab decision-eval --cases evals/decisions/fresh.yaml --cassette evals/decisions/fresh-cassette.json`, labeled before the run): enriching every qualified account bought 12 enrichments, 8 of them wasted; gating on the agent's assessment bought 4, all relevant, none missed, at 414¢ in total (simulated contacts plus observed LLM and Exa) against 960¢. An earlier version, on 16 other cases, missed 2 of 4 relevant accounts because its prompt did not state the disconfirmation rule; that is fixed and documented. Details, the v1 history and the limits: [`agentic-vs-deterministic.md`](agentic-vs-deterministic.md) and [`evals/decisions/REPORT.md`](../evals/decisions/REPORT.md).

## Risk boundaries

- Read-only tools; no URL outside a search result or a known signal page is fetched.
- Page text is passed as untrusted data; a page that tells the model what to conclude is still checked like any other (case `injection-in-page`: the model noted the embedded instruction and the code returned `hypothesis_only`).
- A quote proves what a page says, not that it is true.
- Roles are from the GTM Brain, never people or contact details; nothing is enriched or contacted.
- Approval, idempotent sync and HubSpot checks are unchanged.

## Limits

- The subject check matches names and domains; a common-word company name can still match unrelated pages.
- A disconfirmation search is required before `evidence_backed`, but one search is not exhaustive coverage.
- Semantic support depends on an LLM check: it is constrained (quote and pain only) and fails closed, but it is a model judgment, not a fact.
- Clearing a competing explanation requires reading every result of the search (stated in the prompt; an uncredited check is sent back with the unread URLs).
- A truncated page is marked to the agent, and a `not_relevant` conclusion resting only on truncated pages becomes `insufficient_evidence`.
- The evaluation is small, fictional and labeled by one person; it shows behavior, not business impact.

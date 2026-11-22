# Concepts

This lab is organized around six layers. They are **teaching boundaries**: a way to see where a GTM system stores facts, where it decides, and where it acts. They are not standard industry terms and not six deployed services: here they are Python modules in one package, run by one orchestrator. Each section says which code embodies the layer, and the [learning path](learning-path.md) has one exercise per layer.

```
Data Foundation → Data Layer → GTM Brain → Agentic Intelligence → Activation & Governance → Evaluation
 (what is known)   (how it is    (what we     (bounded research,     (what may be spent,        (did it
                    fetched)      sell, to     optional)               written, approved)         work?)
                                  whom, why)
```

## 1. Data Foundation

What the system knows about accounts and contacts, and how sure it is.

- **Typed records.** `Account`, `ContactProposal`, `Signal`, `CRMAccount` in `src/gtm_lab/models.py` (Pydantic, validated on the way in and out).
- **Canonical identity.** A company is its canonical domain (`util.canonicalize_domain`: lowercase, no scheme, no `www.`). Two rows with the same domain are the same company.
- **Provenance and confidence.** Every record carries a `SourceAttribution` (provider, URL, observed time) and a `Confidence` (`verified`, `secondary`, `approx`, `unknown`). A web page is `secondary`, never `verified`.
- **Known unknowns.** `fields_missing` lists what the provider did not give; `signal_search` says whether signals were searched (`not_searched`, `no_result`, `found`, `failed`, `budget_exhausted`). Not searched is not the same as nothing found.
- **Durable run state.** Every step of a run is persisted in SQLite (`persistence/store.py`) and every event in a JSONL log (`observability/events.py`).

Here it is not a company database or a data warehouse: it is the record shape and the rules that keep facts, sources and gaps apart.

**A quote is not a verified fact.** When research extracts "employs 431 people" from a page, the result is a `SourcedClaim`: what a page says, checked to be on that page. Whether it fills a missing field is decided separately (`FactResolution`: `filled`, `contradicted`, `unresolved`), and provider data is never overwritten by it.

**Dates.** A page's *publication date* (from the search provider), the *observed time* (when we retrieved it) and an *event date* (when the thing happened) are different. Research records an event date only when the quote itself states it.

## 2. Data Layer

How facts are fetched, normalized, enriched, reconciled and checked. In real deployments this overlaps with the foundation; here the line is: the foundation is the record shape, the data layer is the code that fills it.

- **Provider contracts.** `DiscoveryProvider`, `EnrichmentProvider` (`providers/base.py`), `CRMAdapter` (`crm/base.py`), `SignalProvider` (`providers/signals.py`), `EmailFinder` / `EmailVerifier` (`providers/email.py`).
- **Adapters that ship.** Fixtures and a SQLite mock CRM (default, offline); People Data Labs (companies, people), Exa (signals, research), FullEnrich (emails), ZeroBounce (email checks), HubSpot (CRM). All real ones are opt-in flags.
- **Reconciliation.** `workflows/account_discovery.py` matches each qualified account against the CRM by canonical domain (`none`, `exact_domain`, `ambiguous`) and dedups the batch, before any paid call.
- **Source failures** are part of every provider result (`ProviderResult.source_failures`): a failed provider is reported, never read as "no data".

Provider independence is an **interface design**: an adapter for another vendor implements the same abstract class. It is not a promise that any vendor or model can be swapped in YAML; providers are selected by CLI flags and wired in Python.

## 3. GTM Brain

What the organization knows about its commercial strategy, as opposed to raw company and contact data. One versioned YAML file, `examples/account-discovery-to-crm/config/gtm-context.yaml`, loaded into `GTMContext` (`context/models.py`):

- **ICP**: employee band, industry keywords, geographies.
- **Personas**: named roles and the title phrases (`titles_any_of`) the People Data Labs adapter searches for.
- **Signal policy**: which signal kinds count and how fresh they must be.
- **Exclusions** and **operational policy** (budget, approval required, evidence floor).
- **Offers, pain points, buying roles, signal interpretation**: the product, the problems it solves, who in a buying group might care (with an explicit mapping to personas), which misleading explanations to test.
- **Research policy**: how far back research looks, how many accounts and steps it may spend.

It is plain YAML: no vector database, no retrieval service, no memory that learns between runs. Changing it is a diff that can be reviewed.

## 4. Agentic Intelligence

Optional, bounded research where fixed rules are not enough. Off by default; paid when on.

- **Missing company facts** (`--research exa`, `agents/research.py`): for accounts the rules cannot decide, Claude searches and quotes facts; the code checks the quotes, reconciles the claims and re-runs the rules.
- **Opportunity investigation** (`--opportunity exa`, `agents/opportunity.py`): for new qualified accounts, Claude reads the pages behind a signal, searches for supporting and disconfirming evidence, and reports observations with a stance per pain.

What Claude chooses: which searches to run, which returned pages to read, when to stop, what to report. What the code decides: whether a tool call is allowed (only URLs a search returned can be read; budgets checked before each call), whether each quote is on the page and about the right company, whether a semantic reading stands (an independent, constrained check of quote against pain), whether a disconfirmation check counts (the search ran, named the company, every result was read), and the final status. "Autonomous decision-making" here means bounded research and decision *proposals*; nothing the agent says is executed without the deterministic layer below.

**Incomplete research is not negative evidence.** A search with no result, a failed page, an exhausted budget or a truncated page leaves the question open (`checked_no_result`, `incomplete`, `failed`), never "this account does not have the problem".

## 5. Activation & Governance

What may be spent, written and approved. All deterministic code.

- **Enrichment budget** (`EnrichmentBudget`): checked before each call against its maximum cost; an overcharge is booked and flagged, never clamped.
- **Enrichment strategy**: `all_qualified` (default, every new qualified account) or `opportunity_gated` (only accounts the **opportunity action policy**, `policies/opportunity_action.py`, proposes; personas mapped from the supported buying roles).
- **Policies** (`policies/rules.py`): hard checks before an entry can be approved.
- **Human approval**: a plan is `PROPOSED` with a Markdown review artifact; `gtm-lab approve` is a separate command; `gtm-lab sync` is another one.
- **Idempotent writes**: every CRM write carries an external ref from `(run_id, account_id)`; a second sync is all no-ops; a CRM that changed after approval gives `stale_plan`.
- **Existing CRM accounts** can be assessed read-only (`--opportunity-scope all`); nothing is written for them.

## 6. Evaluation & GTM CI/CD

How to know whether a change improved or broke something.

- **Run integrity** (`gtm-lab eval <run-id>`, `evaluations/checks.py`): every account has a decision, budget respected, nothing policy-failed reached the CRM, no duplicate writes. Exit code 1 on failure.
- **Baseline vs agent** (`gtm-lab opportunity-eval`, `gtm-lab decision-eval`, `evaluations/`): hand-labeled fictional cases, a no-AI baseline, a keyword-only control and the agent, on the same inputs; Claude's answers recorded once and replayed for free. Replay keys include the prompt, so a prompt change cannot silently reuse old answers.
- **Failure scenarios** (`tests/scenarios/`): 13 named failure modes, each a test.
- **Real-data case** (`tests/realcase/`, opt-in): record real provider answers once, replay them offline, compare to expected results kept in a private directory.

CI runs all of this offline: no network, no paid call.

## Glossary

**ICP (Ideal Customer Profile).** The firmographic shape of a company worth talking to.

**Buyer persona.** A named role the enrichment provider is asked for ("VP Sales"), with title phrases (`titles_any_of`) that the People Data Labs adapter matches in job titles; the fixture adapter matches by persona name.

**Buying signal.** A dated event about a company (`sales_hiring`, `new_sales_leadership`, `revenue_expansion`). An observation, not intent.

**Qualification decision.** `qualified`, `not_qualified` or `insufficient_evidence`, with evidence, missing fields, confidence and `decided_by`: `rules`; `hybrid` (rules re-run on facts the research loop checked); `llm` (an `--agentic` opinion, always `insufficient_evidence`, its verdict kept in `missing`). Only the rules ever produce `qualified` or `not_qualified`.

**Opportunity assessment.** Separate from qualification: `evidence_backed`, `needs_research`, `not_relevant`, `hypothesis_only` or `insufficient_evidence`, with observations, hypotheses, buying-role hypotheses, coverage and cost. It never changes the ICP decision.

**Reconciliation.** Matching a discovered account against the CRM: `none` (new), `exact_domain`, `ambiguous` (several CRM accounts, or a duplicate in the batch).

**WritePlan.** The proposed CRM operations of a run: `DRAFT → PROPOSED → APPROVED | REJECTED → SYNCED | PARTIAL | FAILED`. Only entries with no policy failure are approvable.

**Review artifact.** The Markdown file a person reads before `approve` or `reject`.

**Idempotent write.** Same result whether it runs once or many times.

**Stale plan.** An entry whose CRM state changed between approval and sync; nothing is written for it.

**Audit trail.** The append-only JSONL log of every decision, call, cost and outcome, queryable by run id.

**Offline vs paid modes.** Offline (`gtm-lab demo --offline`, default): fixtures, mock CRM, rules, no key. `--agentic`, `--research exa`, `--opportunity exa` and the real providers are opt-in and paid; the Python program calls the APIs with its own keys, separately from a Claude Code session.

# Concepts

Short glossary of the specific terms this repo uses. Short on purpose.

## GTM Context

A versioned YAML description of **what the company knows about its market**: its ICP, its personas, which buying signals it cares about, which companies to exclude, and the operational policies the workflow must respect. One file, diff-able, agent-readable.

## ICP (Ideal Customer Profile)

The firmographic shape of a company worth talking to. In this repo: an employee range, a list of industry keywords, a list of geographies, and a sales motion label. The qualifier compares each discovered account to this shape.

## Buyer persona

A named role within an ICP account that the GTM team wants to find a contact for. Here: "VP Sales", "Head of Sales", etc. The persona name is what the enrichment provider is asked for. Each persona also lists title substrings (`titles_any_of`), which no code reads yet; they are there for a real provider adapter.

## Buying signal

A dated event about a company that raises the odds it is in-market. Example: "hired a new VP Sales" (`new_sales_leadership`). Signals have a kind, a description, an event date, and a source. A signal older than `max_signal_age_days` is stale and does not count as evidence.

## Qualification decision

The result of asking "does this account match the ICP?" One of three values: `qualified`, `not_qualified`, `insufficient_evidence`. Always carries explicit evidence, missing fields, a confidence value, and a `decided_by` label (`rules` or `llm`).

## Reconciliation

The step where a discovered account is matched against the CRM. Four possible outcomes: `none` (fresh), `exact_domain` (one hit), `normalized_domain` (reserved for future suffix normalization), `ambiguous` (two or more CRM accounts share the domain). Also covers in-batch dedup - if two newly-discovered rows point at the same company, the later row is `ambiguous`.

## Enrichment

Adding contact-level data to a qualified account. In this repo: calling an `EnrichmentProvider` with the account and a list of persona names, getting back `ContactProposal` rows. Every call has a cents cost.

## Enrichment budget

A cents counter enforced BEFORE the provider is called. The brief is explicit: this is deterministic code, not an LLM prompt.

## WritePlan

The proposed set of CRM operations for one run. Status machine: `DRAFT → PROPOSED → (APPROVED | REJECTED) → (SYNCED | PARTIAL | FAILED)`. Only entries that passed every policy check are "approvable".

## Policy check

A function that returns a list of human-readable reasons an entry should not be promoted. Empty list = passes. Five checks ship by default; adding one is 15 lines and a test.

## Review artifact

The Markdown document rendered from a `PROPOSED` plan. It is what a human reads before deciding to call `approve` or `reject`. No new UI - a Markdown file is enough.

## Idempotent write

A write that produces the same result whether it's called once or many times. In this repo: every CRM write carries an `external_ref` key derived from `(run_id, account_id)`. The adapter returns the existing record for a key it's seen before.

## Audit trail

The append-only JSONL log of every decision, call, cost, and outcome. One line per event; `event_type` is dotted; payload is JSON. Queryable by `run_id`.

## Evaluation (run-level)

Four integrity checks over a completed run: every account has a decision, budget respected, no policy-failed entry reached the CRM, no duplicate account writes. `gtm-lab eval <run-id>` runs them. Returns exit code 1 on any failure - useful in CI.

## Offline mode vs agentic mode

- **Offline mode** (`gtm-lab demo --offline`, default): no API keys needed, no paid calls, synthetic fixtures. Deterministic qualifier. Mock CRM. Fully reproducible.
- **Agentic mode** (`gtm-lab demo --agentic`): accounts the rules return as `insufficient_evidence` are sent to Claude (`claude-opus-5-5`) under hard call and spend caps. Each LLM decision is logged next to the rules baseline. Without credentials it runs offline and says so.

## Stale plan

A write-plan entry whose CRM state changed between approval and sync: another writer created the same domain in the meantime. Sync reports it as `stale_plan`, writes nothing for that entry (not the account, not its contacts), and leaves the plan `PARTIAL`. The fix is a new plan, not a retry.

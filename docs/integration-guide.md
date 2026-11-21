# Integration guide

How to reuse parts of this repo without adopting all of it.

## Reuse pattern 1 - budget enforcement in any Python workflow

```python
from gtm_lab.workflows import EnrichmentBudget

budget = EnrichmentBudget(cap_cents=500)  # $5.00
for lead in leads_to_enrich:
    cost = provider.cost_cents_per_call
    if not budget.can_spend(cost):
        logger.info("Budget exceeded - stopping enrichment", remaining=budget.remaining)
        break
    result = provider.enrich(lead)
    budget.charge(cost)
```

The budget is a plain dataclass. Use it anywhere you call a paid provider in a loop.

## Reuse pattern 2 - CRM reconciliation layer

```python
from gtm_lab.workflows import Reconciliation
from gtm_lab.util import canonicalize_domain

recon = Reconciliation()
matches = recon.reconcile(my_discovered_accounts, my_crm_adapter)
new_only = [m for m in matches if m.match_type == "none"]
existing = [m for m in matches if m.match_type == "exact_domain"]
ambiguous = [m for m in matches if m.match_type == "ambiguous"]
# Enrich and sync `new_only`; surface `ambiguous` for human review.
```

Your `my_crm_adapter` only needs to implement `find_accounts_by_domain(domain)`.

## Reuse pattern 3 - policy evaluation before any CRM write

```python
from gtm_lab.policies import evaluate_entry

failures = evaluate_entry(
    account=account,
    decision=qualification_decision,
    entry=proposed_write,
    context=gtm_context,
)
if failures:
    # Don't write; surface failures to the operator
    audit.log("policy-rejected", failures=failures)
    return
```

Add your own checks: edit `src/gtm_lab/policies/rules.py`. Each new rule is one short function returning `list[str]`.

## Reuse pattern 4 - human approval gate via a Markdown artifact

```python
from gtm_lab.review import render_review

review_md = render_review(plan, store)
Path("pending-review.md").write_text(review_md)
# Have a human read it, then:
plan.status = "approved" if approved else "rejected"
```

Works in a terminal, a GitHub PR, a Slack message - anything that renders Markdown.

## Reuse pattern 5 - post-run evaluation in CI

```python
from gtm_lab.evaluations import evaluate_run
report = evaluate_run(run_id, store)
assert report.ok, f"Run {run_id} failed integrity checks: {report.failures}"
```

Drop into any end-of-run gate. Four checks, zero external dependencies.

## Implementing a real provider

### Discovery

```python
from gtm_lab.providers.base import DiscoveryProvider, ProviderResult
from gtm_lab.models import Account, SourceAttribution

class ClayDiscoveryProvider(DiscoveryProvider):
    name = "clay"

    def __init__(self, api_key: str, table_id: str): ...

    def discover(self, *, limit: int) -> ProviderResult:
        rows = self._call_clay_api(limit=limit)
        accounts = [self._row_to_account(r) for r in rows]
        failures = []  # extend with real error paths
        return ProviderResult(
            accounts=accounts,
            source_failures=failures,
            search_coverage={"clay": len(accounts)},
        )
```

### CRM

A real adapter ships for HubSpot: `src/gtm_lab/crm/hubspot.py`, documented in [`hubspot.md`](hubspot.md). Use it as the template for another CRM (Salesforce, Pipedrive...). The parts that carry the guarantees:

- `name`: recorded on each plan, so a plan is only ever synced to the CRM it was reconciled against.
- `find_account_by_external_ref` / `find_contact_by_external_ref`: a direct lookup on a unique custom field. This is what makes re-syncs no-ops.
- `create_account`: refuse (non-retriable `CRMError`) when another record owns the domain; treat a unique-field conflict as "already written".
- Map rate limits and 5xx to `CRMError(retriable=True)`, other client errors to `retriable=False`.

Check your CRM's real behavior before relying on it. HubSpot, for instance, accepts duplicate domains and its search lags writes by a few seconds; both are documented in `hubspot.md`.

### Enrichment

```python
from gtm_lab.providers.base import EnrichmentProvider

class ApolloEnrichment(EnrichmentProvider):
    name = "apollo"
    cost_cents_per_call = 3  # whatever your plan says

    def enrich(self, account, personas):
        ...
```

Drop any combination into `AccountDiscoveryWorkflow` - the orchestrator doesn't care.

## Minimal recipe for a new workflow

If you want a different business workflow (say, lead qualification without CRM writes):

```python
from gtm_lab.context import load_context
from gtm_lab.agents.qualifier import DeterministicQualifier
from gtm_lab.providers import FixtureDiscoveryProvider

ctx = load_context("my-context.yaml")
disc = FixtureDiscoveryProvider("my-leads.yaml")
q = DeterministicQualifier()

for acc in disc.discover(limit=100).accounts:
    decision = q.qualify(acc, ctx)
    print(acc.name, decision.decision, decision.confidence)
```

That's a 5-line qualifier using 3 reusable components from this repo.

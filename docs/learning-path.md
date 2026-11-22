# Learning path

Six modules, one per layer of [`concepts.md`](concepts.md). Everything here runs offline: no API key, no Claude subscription, no live CRM. Agentic behavior is studied by replaying answers Claude gave once, recorded in `evals/`.

## Setup

```bash
git clone https://github.com/cuspofaries/agentic-gtm-systems-lab.git
cd agentic-gtm-systems-lab
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export GTM_LAB_STATE_DIR=/tmp/gtm-lab-learning   # keep exercise state out of the repo
```

Two routes, same code:

- **Standalone CLI**: run the commands below.
- **Guided, in Claude Code**: start `claude` in the repository and type `/gtm-lab`. It runs the same offline commands and explains them from the persisted data; `/gtm-lab inspect <run-id>`, `/gtm-lab explain <run-id> <account-id>`, `/gtm-lab eval <run-id>`, `/gtm-lab opportunity <run-id>`. It asks before every state change. Your Claude Code login is all it needs (see [`claude-code.md`](claude-code.md)).

Module 5 is the only one with a human approval; it uses the local mock CRM only. Do not point these exercises at a real CRM.

Requirements: a POSIX shell (the snippets use `export`, `$(...)`, `sed -i` and heredocs; on macOS use `sed -i ''`), Python 3.12. Every command and expected outcome on this page is checked offline in CI by `tests/integration/test_learning_path.py`, which also fails if the page quotes a `gtm-lab` option or a `pytest -k` selection that no longer exists.

---

## 1. Data Foundation: identity, provenance, unknowns

**Concept.** A record is useful only if you know where it came from and what it does not say. A canonical domain is the company's identity; `fields_missing` and `signal_search` make gaps explicit; a page quote is a claim about what a page says, not a verified fact.

**Why it matters to a GTM team.** A GTM team pays for every wrong record twice: once to enrich it, once when a rep acts on it. Knowing what is missing and where each fact came from is what lets the rest of the system abstain instead of guessing.

**Where it lives.** First layer: everything downstream reads these records (see [`architecture.md`](architecture.md), step 1 to 3).

**Code location.** `src/gtm_lab/models.py` (`Account`, `SourceAttribution`, `Confidence`, `SourcedClaim`, `FactResolution`), `src/gtm_lab/util.py` (`canonicalize_domain`), `src/gtm_lab/persistence/store.py`.

**Run it.**

```bash
gtm-lab demo --offline --json > /tmp/run.json
RUN=$(python -c "import json;print(json.load(open('/tmp/run.json'))['run_id'])")
gtm-lab inspect $RUN | python -c "
import json,sys; d=json.load(sys.stdin)
a=[x for x in d['accounts'] if x['account_id']=='acc_005'][0]
print(a['domain'], a['employees'], a['fields_missing'], a['signal_search'], a['source'])
print([x for x in d['decisions'] if x['account_id']=='acc_005'][0]['decision'])"
```

**Observe.** `acc_005` has `employees: None`, `fields_missing: ['employees', 'hq_state']`, a `fixture-discovery` source, and the decision `insufficient_evidence`: the rules refuse to guess.

**Break it.** Copy the example (`cp -r examples/account-discovery-to-crm /tmp/my-example`), set `employees: 120` and `fields_missing: []` for `acc_005` in `/tmp/my-example/fixtures/discovery.yaml`, and rerun `gtm-lab demo --offline --example-dir /tmp/my-example`. The account is now qualified: the decision follows the data, so bad data gives confident wrong decisions.

**Build it.** In the same copy (with `acc_005` now at 120 employees), change its `domain` to `WWW.Brightpath.saas.example/`, a different spelling of `acc_001`'s domain, and rerun the demo on the copy. `canonicalize_domain` turns it into `brightpath.saas.example`, and the plan skips `acc_005` as an "Ambiguous CRM match (candidates: ['acc_001'])" instead of creating a second company.

**Test it.** `pytest tests/unit/test_util.py tests/unit/test_qualifier.py` checks domain canonicalization and the missing-data behavior. `pytest tests/unit/test_research_loop.py -k "provider_data or disagreeing"` shows that a research claim never overwrites provider data and that disagreeing sources stay `contradicted`.

**Critical question.** If a page says "about 431 employees" and another "189", which is the fact? Here neither: both are claims, and the field stays open. What would you need to call one of them verified?

---

## 2. Data Layer: providers, reconciliation, duplicates, failures

**Concept.** The data layer fetches and cleans facts behind interfaces. Reconciliation against the CRM happens before any paid call. A provider failure is reported, not read as "no data".

**Why it matters to a GTM team.** Duplicate accounts and paid enrichment of companies already in the CRM are among the most common GTM data costs; reconciliation before spending prevents both.

**Where it lives.** Steps 1, 2, 4 and 7 of the run: discovery, signals, CRM reconciliation, enrichment.

**Code location.** `src/gtm_lab/providers/base.py` (`DiscoveryProvider`, `EnrichmentProvider`, `ProviderResult`), `providers/fixture_discovery.py`, `providers/pdl.py`, `crm/base.py`, `crm/mock.py`, reconciliation in `workflows/account_discovery.py`.

**Run it.** With the run of module 1:

```bash
gtm-lab inspect $RUN | python -c "
import json,sys; d=json.load(sys.stdin)
print([(e['account_id'], e['crm_action'], e['skip_reason']) for e in d['plan']['entries'] if e['skipped']])"
```

**Observe.** `acc_003` is `skip_existing` ("Already exists in CRM": it is in `fixtures/crm-seed.yaml`) and `acc_004_dup` is skipped as an ambiguous in-batch duplicate of `acc_004`. Neither is enriched.

**Break it.** `pytest tests/scenarios/test_scenarios.py -k "already_in_crm or duplicate or interrupted"` runs three named failure scenarios; read their assertions in `tests/scenarios/test_scenarios.py` and the catalogue in [`failure-scenarios.md`](failure-scenarios.md).

**Build it.** Write an `EnrichmentProvider` that returns one fixed contact (see [`integration-guide.md`](integration-guide.md), "Implementing a real provider"); plug it into a test with the `make_workflow` fixture of `tests/conftest.py`, as `tests/unit/test_opportunity_action.py` does with `CountingEnrichment`.

**Test it.** `pytest tests/unit/test_reconciliation.py tests/unit/test_mock_crm.py tests/unit/test_pdl_provider.py` (PDL against a fake transport that reproduces measured behavior).

**Critical question.** The fixture provider and the PDL adapter implement the same interface. What does that buy you, and what does it not (rate limits, credits, missing emails on PDL's free plan)?

---

## 3. GTM Brain: commercial knowledge as a reviewable file

**Concept.** The ICP says which companies fit; the offer, pains and buying roles say why one might buy and who might care. Both live in one YAML file that changes by diff.

**Why it matters to a GTM team.** ICP and positioning usually live in slides and heads; as a versioned file they become testable, reviewable and the same for every run.

**Where it lives.** Read by every step; the only file most teams need to change to adapt the lab.

**Code location.** `examples/account-discovery-to-crm/config/gtm-context.yaml`, `src/gtm_lab/context/models.py` (`GTMContext`, `Offer`, `PainPoint`, `BuyingRole`), every key in [`configuration.md`](configuration.md).

**Run it.** Always on a copy:

```bash
cp -r examples/account-discovery-to-crm /tmp/brain-example
sed -i 's/^  employees_max: 200/  employees_max: 100/' /tmp/brain-example/config/gtm-context.yaml
gtm-lab demo --offline --json --example-dir /tmp/brain-example > /tmp/run2.json
RUN2=$(python -c "import json;print(json.load(open('/tmp/run2.json'))['run_id'])")
gtm-lab inspect $RUN2 | python -c "
import json,sys; print([(x['account_id'], x['decision']) for x in json.load(sys.stdin)['decisions']])"
```

**Observe.** Accounts above 100 employees (`acc_003` at 140, `acc_004` at 120 and its duplicate) become `not_qualified`; compare with the decisions of module 1.

**Break it.** In the copy, add `personas: [Chief Vibes Officer]` to one buying role and run the demo again: the context fails to load with "unknown pain point id(s) or persona name(s)". The brain is validated before anything runs.

**Build it.** Add a pain point to the copy (an `id`, a `description`, `evidence_terms`) and reference it from the offer's `pains` and a buying role's `pains`. Load it with `python -c "from gtm_lab.context import load_context; print(load_context('/tmp/brain-example/config/gtm-context.yaml').offers[0].pains)"`.

**Test it.** `pytest tests/unit/test_opportunity_action.py -k "unknown_persona or mapping"` (persona mapping and its validation).

**Critical question.** Which belongs in the brain and which in the data: "we sell onboarding software to teams that hire sales reps fast" or "Acme has 85 employees"? What breaks if they are mixed?

---

## 4. Agentic research: what the agent chooses, what the code checks

**Concept.** For accounts the rules cannot judge, Claude searches, reads and reports observations. The code checks every quote, has each supporting quote judged independently against the pain, and credits a disconfirmation check only if it was really performed.

**Why it matters to a GTM team.** Signals like hiring or funding are cheap to find and easy to over-read; the value is in telling the one that matters for your offer from the many that do not, and in admitting when the pages do not say.

**Where it lives.** Steps 3 and 5 of the run, both optional: research for missing facts, opportunity investigation.

**Code location.** `src/gtm_lab/agents/research_tools.py` (tools, budgets, loop), `agents/opportunity.py`, `evaluations/opportunity_eval.py` (the fictional web), `evals/decisions/fresh.yaml` (cases and labels).

**Run it.** Replay Claude's recorded answers on 12 fresh cases:

```bash
gtm-lab opportunity-eval --cases evals/decisions/fresh.yaml --cassette evals/decisions/fresh-cassette.json
```

Then look at one supported claim and one disconfirmed one:

```bash
python - <<'EOF'
from pathlib import Path
from gtm_lab.context import load_context
from gtm_lab.evaluations.opportunity_eval import LLMCassette, run_eval

ctx = load_context("examples/account-discovery-to-crm/config/gtm-context.yaml")
run = run_eval(Path("evals/decisions/fresh.yaml"), ctx,
               LLMCassette(Path("evals/decisions/fresh-cassette.json"), "replay"))
for r in run.results:
    if r.case["id"] in ("f-relevant-cro-quote", "f-abstain-freeze-search"):
        a = r.agent
        print(r.case["id"], "| baseline:", r.baseline.assessment_status, "| agent:", a.assessment_status)
        for o in a.observed_events:
            print("  ", o.check, o.stance, o.pain_id, "|", o.quote)
        for m in a.semantic_matches:
            print("   semantic:", m.validation, "| keyword hit:", m.lexical_term_hit)
        for c in a.disconfirmation_checks:
            print("   disconfirmation:", c.credited, "|", c.reason)
EOF
```

**Observe.** In `f-relevant-cro-quote` the supporting quote ("new reps currently need about six months before they can sell on their own...") contains none of the pain's keywords; the semantic check verified it, and the disconfirmation search returned nothing ("not found in checked sources; not proof"). In `f-abstain-freeze-search`, a search found a layoff page; the contradiction is credited and the status is `needs_research`. The signal-kind baseline calls both `evidence_backed`.

**Break it.** `pytest tests/unit/test_opportunity.py -k "superficial or not_read or keyword_stuffing or truncated"`: a search that does not name the company, results left unread, a quote the verifier rejects, a truncated page. Each test shows a case where the agent's answer alone would have been accepted without the code.

**Build it.** In a copy of `evals/decisions/fresh.yaml`, change a case's `label.relevant`, and rerun the replay with `--cases` pointing at the copy: the metrics move, the agent's answers do not. Labels are an input you own. (Changing page text instead makes the replay miss: new conversations need a paid `--record`.)

**Test it.** `pytest tests/unit/test_research_loop.py tests/unit/test_opportunity.py`.

**Critical question.** The agent chose which pages to read; the code decided what counts. Which of the two would you trust less on real, messy pages, and why?

---

## 5. Activation & Governance: what gets bought, approved and written

**Concept.** Spending and writing are deterministic: a budget, an action policy, hard checks, a human approval, idempotent writes. Gating enrichment on the assessment saves contacts and can also miss opportunities.

**Why it matters to a GTM team.** This is where money is spent and records are written. Keeping it deterministic and approved by a person is what makes an agentic system safe to run on a real CRM.

**Where it lives.** Steps 6 to 9, then `approve` and `sync`.

**Code location.** `src/gtm_lab/policies/opportunity_action.py`, `policies/rules.py`, `EnrichmentBudget` and sync in `workflows/account_discovery.py`, `review.py`.

**Run it.**

```bash
gtm-lab demo --offline --enrichment-strategy opportunity_gated --json > /tmp/run3.json
RUN3=$(python -c "import json;print(json.load(open('/tmp/run3.json'))['run_id'])")
gtm-lab inspect $RUN3 | python -c "
import json,sys
for e in json.load(sys.stdin)['plan']['entries']:
    print(e['account_id'], e['enrichment_status'], (e.get('opportunity_action') or {}).get('assessment_status'))"
gtm-lab decision-eval --cases evals/decisions/fresh.yaml --cassette evals/decisions/fresh-cassette.json
```

**Observe.** Offline, no account is assessed, so gated mode enriches nothing: every new account is `deferred_by_policy` with `not_assessed` (fail closed; "not assessed" is never "not relevant"). The decision replay shows the trade on the fresh cases: enriching everyone vs gating, enrichments, wasted ones, missed relevant accounts, simulated contact cost and observed LLM and search cost.

Now the approval, on the mock CRM of module 1's run:

```bash
cat $GTM_LAB_STATE_DIR/review_$RUN.md     # what a person reads
gtm-lab approve $RUN                      # the human decision; writes nothing
gtm-lab sync $RUN --json                  # writes to the local mock CRM
gtm-lab sync $RUN --json                  # second run: all idempotent no-ops
```

**Break it.** `pytest tests/scenarios/test_scenarios.py -k "budget or rejection or changed_after_approval"` and `pytest tests/unit/test_budget.py -k overcharge`.

**Build it.** Add a rule to `evaluate_entry` in `policies/rules.py` (for example: refuse an entry whose account has no `hq_state`) and a test next to the scenarios.

**Test it.** `pytest tests/unit/test_opportunity_action.py` (every branch of the action matrix, and the provider calls actually made).

**Critical question.** On the fresh cases, gating cost less and wasted nothing; on the earlier 16 cases, an older version missed half the relevant accounts. Who in a GTM team should own the trade between wasted credits and missed accounts?

---

## 6. Evaluation: what improved, what regressed

**Concept.** A change is better only if it is measured on cases it was not tuned on, against a baseline, with costs. Small fictional sets show behavior, not rates.

**Why it matters to a GTM team.** Without a baseline and labeled cases, an agent that sounds convincing is indistinguishable from one that is right.

**Where it lives.** `gtm-lab eval` after a run; `opportunity-eval` and `decision-eval` on labeled cases; the test suite in CI.

**Code location.** `evaluations/checks.py`, `evaluations/opportunity_eval.py`, `evaluations/decision_eval.py`, `evals/`, `tests/`.

**Run it.**

```bash
ruff check .
pytest -q
gtm-lab eval $RUN
gtm-lab decision-eval                       # 16 decision cases, current agent
gtm-lab decision-eval --cases evals/decisions/fresh.yaml --cassette evals/decisions/fresh-cassette.json
```

**Observe.** `eval` checks the run's integrity. The two decision replays are the current agent on two sets; read their reports (`evals/decisions/REPORT.md`, `evals/decisions/FRESH_REPORT.md`) and the history (`evals/opportunity/v1/`, `evals/decisions/v2/`). Which numbers come from cases that were seen before a fix is stated in [`agentic-vs-deterministic.md`](agentic-vs-deterministic.md).

**Break it.** Edit one line of `SYSTEM_PROMPT` in `agents/opportunity.py` and run `gtm-lab decision-eval`: it exits 1 with replay misses. Recordings are keyed on the prompt, so a prompt change cannot reuse old answers silently. Revert the edit.

**Build it.** Write two new cases in a new file, in the format of `evals/decisions/fresh.yaml`, labels first. Running them needs a paid recording (`--record`, Claude and the fictional web only); until then, `pytest tests/integration/test_opportunity_eval.py` shows how the harness records and replays with a scripted, free stand-in.

**Test it.** `pytest tests/integration/test_opportunity_eval.py` (record then replay gives identical metrics; the committed recordings replay without a miss and keep the code's invariants).

**Critical question.** The current agent was re-recorded on cases whose failures motivated its last change. Why are those numbers weaker evidence than the fresh set, even when they look the same?

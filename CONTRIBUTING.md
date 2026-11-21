# Contributing

Thanks for looking at this. Three fast paths to a useful PR:

## 1. Fix a real GTM workflow bug you hit

The surest sign a change is worth merging is that it stops something going wrong in the wild. Open an issue describing the failure first - one line is fine ("duplicate contact created on retry with external_ref=X"). A test that reproduces the issue is more valuable than the fix.

## 2. Add a provider adapter

Clay / Apollo / Crustdata / HubSpot / Salesforce - pick one. See `docs/integration-guide.md`. Add your adapter under `src/gtm_lab/providers/` (or `src/gtm_lab/crm/`), ship a fixture-based test, and don't ship live credentials. Mark it as **Implemented but not integration-tested** in the README capabilities table until a real user confirms it against their account.

## 3. Add a policy check

`src/gtm_lab/policies/rules.py`. Each rule is one short function that returns `list[str]`. Add one test in `tests/scenarios/test_scenarios.py` that exercises the fail path.

## Running the suite locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest -q
```

## Style

- Python 3.12. `from __future__ import annotations` everywhere.
- Pydantic for data contracts. Dataclasses for pure compute helpers.
- No magic. No metaclass tricks. No decorators invented for this repo.
- No new dependencies without a note in the ADR directory.
- Docstrings explain *why*. Code explains *what*.
- Don't add "TODO" comments; file an issue instead.

## What NOT to PR

- A dashboard. See the brief: the workflow must be reliable before the dashboard goes anywhere.
- A multi-agent orchestration framework. One business workflow is the point.
- "Accuracy improved by X%" claims without a labeled ground-truth dataset.
- Any live LLM call in the default `demo` command. Keep offline offline.
